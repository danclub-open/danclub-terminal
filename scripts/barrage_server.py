#!/usr/bin/env python3
"""DanClub Terminal — Lightweight Live Danmaku & Frontline Message Board Backend (MIT License).

Zero third-party dependencies (Python 3.9+ stdlib only).
Designed to run behind Caddy on 127.0.0.1:18820 with SQLite WAL persistence,
HMAC-hashed IP rate limiting (zero raw IP PII stored), anti-XSS/anti-spam filtering,
pre-thread bounded admission control, and Origin-guarded CORS support for both
the primary mirror and GitHub Pages.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unicodedata
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

ALLOWED_SIDES = frozenset({"LONG", "SHORT", "NEUTRAL"})
ALLOWED_COINS = frozenset({"BTC", "ETH", "SOL", "HYPE", "ALL"})
LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
DEFAULT_WRITE_ORIGINS = frozenset(
    {
        "https://danclub.duckdns.org",
        "https://danclub-open.github.io",
    }
)

MAX_SQLITE_INT64 = (1 << 63) - 1
MAX_POST_BYTES = 4096
MAX_LIKE_BYTES = 1024
MAX_TEXT_LEN = 80
MAX_NICK_LEN = 16
DEFAULT_NICK = "匿名战友"
MAX_STORED_MESSAGES = 2000
RETENTION_PROTECT_SEC = 25 * 3600  # Always preserve full 24h stats + 1h rate-limit history
WINDOW_SYNC_LIMIT = 80
MAX_CONCURRENT_REQUESTS = 64
SOCKET_TIMEOUT_SEC = 6.0
REQUEST_DEADLINE_SEC = 6.0

# Per-IP & global rate limits
COOLDOWN_SEC = 4
WINDOW_5M_SEC = 300
MAX_POSTS_5M = 12
WINDOW_1H_SEC = 3600
MAX_POSTS_1H = 40
DUP_GUARD_SEC = 60
GLOBAL_WINDOW_SEC = 60
GLOBAL_MAX_POSTS_PER_MIN = 120
LIKE_WINDOW_SEC = 60
MAX_LIKES_PER_MIN = 30

# Strip ASCII control chars (excluding whitespace \t\n\r collapsed below), C1 controls, and bidi/zero-width spoofing chars
_STRIP_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)
_MULTI_SPACE_RE = re.compile(r"\s+")
_REPEAT_CHAR_RE = re.compile(r"(.)\1{9,}")

# Anti-spam / anti-phishing / anti-ad link patterns
_SPAM_URL_RE = re.compile(
    r"(?:https?://|www\.|\bt\.me/|\bdiscord\.gg/|\bqm\.qq\.com/|"
    r"[a-z0-9-]{2,}\.(?:com|cn|net|org|io|xyz|top|vip|cc|me|gg|app|site|shop|club)\b)",
    re.IGNORECASE,
)
_SPAM_SOLICIT_RE = re.compile(
    r"(?:加\s*[v微扣q]|微\s*信\s*号|vx\s*[:：]|wx\s*[:：]|qq\s*群|加群领|免费领\s*u|"
    r"带单老师|稳赚不赔|包赔带飞|返佣链接|助记词|私钥发我|私聊领空投|点击领取空投|"
    r"(?<!\d)1[3-9]\d{9}(?!\d))",
    re.IGNORECASE,
)

_BUSY_503_BODY = b'{"ok":false,"error":"\xe6\x9c\x8d\xe5\x8a\xa1\xe5\x99\xa8\xe7\xb9\x81\xe5\xbf\x99\xef\xbc\x8c\xe8\xaf\xb7\xe7\xa8\x8d\xe5\x90\x8e\xe5\x86\x8d\xe8\xaf\x95"}'
_BUSY_503_RESP = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Content-Type: application/json; charset=utf-8\r\n"
    b"Cache-Control: no-store\r\n"
    b"Access-Control-Allow-Origin: *\r\n"
    b"Connection: close\r\n"
    + f"Content-Length: {len(_BUSY_503_BODY)}\r\n\r\n".encode("ascii")
    + _BUSY_503_BODY
)


def has_surrogates(raw: Any) -> bool:
    """Detect unpaired UTF-16 surrogate code points before touching SQLite."""
    if not isinstance(raw, str):
        return False
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in raw)


def sanitize_text(raw: Any) -> str:
    """Normalize and strip dangerous control/bidi/zero-width characters."""
    if not isinstance(raw, str):
        return ""
    cleaned = _STRIP_CHARS_RE.sub("", raw)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _normalized_for_spam_check(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def check_spam(nick: str, text: str) -> Optional[str]:
    """Return user-friendly Chinese rejection reason if spam/abuse detected, else None."""
    combined = f"{nick} {text}"
    norm = _normalized_for_spam_check(combined)
    if _SPAM_URL_RE.search(norm):
        return "请勿发布外链或网址，专注盘面交流"
    if _SPAM_SOLICIT_RE.search(norm):
        return "请勿发布引流、广告或联系方式"
    if _REPEAT_CHAR_RE.search(_normalized_for_spam_check(text)):
        return "请勿发送大段重复刷屏字符"
    return None


def load_or_create_secret(secret_path: Path) -> bytes:
    """Load exact 32-byte binary HMAC key verbatim (no .strip()) or publish atomically."""
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.exists():
        data = secret_path.read_bytes()
        if len(data) >= 32:
            return data
        raise ValueError(f"HMAC secret file {secret_path} is shorter than 32 bytes ({len(data)})")

    raw = secrets.token_bytes(32)
    fd, tmp_name = tempfile.mkstemp(prefix=".hmac.tmp.", dir=str(secret_path.parent))
    try:
        os.chmod(tmp_name, 0o600)
        view = memoryview(raw)
        written = 0
        while written < len(raw):
            n = os.write(fd, view[written:])
            if n <= 0:
                raise OSError("short write while creating HMAC secret")
            written += n
        os.fsync(fd)
        os.close(fd)
        fd = -1
        try:
            os.link(tmp_name, str(secret_path))
            return raw
        except FileExistsError:
            data = secret_path.read_bytes()
            if len(data) >= 32:
                return data
            raise
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def parse_strict_int(raw: Any, *, min_val: int, max_val: int = MAX_SQLITE_INT64) -> Optional[int]:
    """Accept only genuine JSON integers (rejecting bools, floats, strings, and overflow)."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    if raw < min_val or raw > max_val:
        return None
    return raw


class BarrageStore:
    """Thread-safe SQLite WAL persistence with privacy-preserving HMAC IP rate limiting."""

    def __init__(self, db_path: Path, hmac_secret: bytes) -> None:
        self.db_path = db_path
        self.hmac_secret = hmac_secret
        self._lock = threading.Lock()
        self._insert_counter = 0
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA temp_store=MEMORY;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS barrage_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    coin TEXT NOT NULL,
                    side TEXT NOT NULL,
                    nick TEXT NOT NULL,
                    text TEXT NOT NULL,
                    norm_text TEXT NOT NULL DEFAULT '',
                    ip_hash TEXT NOT NULL,
                    likes INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_barrage_created
                    ON barrage_messages(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_barrage_hidden_id
                    ON barrage_messages(hidden, id DESC);
                CREATE INDEX IF NOT EXISTS idx_barrage_ip_created
                    ON barrage_messages(ip_hash, created_at DESC);

                CREATE TABLE IF NOT EXISTS barrage_likes (
                    msg_id INTEGER NOT NULL,
                    ip_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (msg_id, ip_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_likes_ip_created
                    ON barrage_likes(ip_hash, created_at DESC);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def hash_ip(self, client_ip: str) -> str:
        ip_str = (client_ip or "unknown").strip()
        return hmac.new(self.hmac_secret, ip_str.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

    def _stats_24h_unlocked(self, now_ts: int) -> Dict[str, Any]:
        cutoff = now_ts - 86400
        rows = self._conn.execute(
            """
            SELECT side, COUNT(*) AS cnt
            FROM barrage_messages
            WHERE hidden = 0 AND created_at >= ?
            GROUP BY side
            """,
            (cutoff,),
        ).fetchall()
        counts = {"LONG": 0, "SHORT": 0, "NEUTRAL": 0}
        for r in rows:
            s = str(r["side"])
            if s in counts:
                counts[s] = int(r["cnt"])
        total = counts["LONG"] + counts["SHORT"] + counts["NEUTRAL"]
        ls_total = counts["LONG"] + counts["SHORT"]
        long_pct = round(counts["LONG"] * 100.0 / ls_total, 1) if ls_total > 0 else 50.0
        short_pct = round(100.0 - long_pct, 1) if ls_total > 0 else 50.0
        return {
            "total": total,
            "long": counts["LONG"],
            "short": counts["SHORT"],
            "neutral": counts["NEUTRAL"],
            "long_pct": long_pct,
            "short_pct": short_pct,
        }

    def _latest_id_unlocked(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS mid FROM barrage_messages WHERE hidden = 0"
        ).fetchone()
        return int(row["mid"]) if row else 0

    @staticmethod
    def _row_to_public(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "ts": int(row["created_at"]),
            "coin": str(row["coin"]),
            "side": str(row["side"]),
            "nick": str(row["nick"]),
            "text": str(row["text"]),
            "likes": int(row["likes"]),
        }

    def health(self) -> Dict[str, Any]:
        now_ts = int(time.time())
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM barrage_messages WHERE hidden = 0"
            ).fetchone()
            stats = self._stats_24h_unlocked(now_ts)
            return {
                "ok": True,
                "server_time": now_ts,
                "total": int(row["cnt"]) if row else 0,
                "last_24h": stats["total"],
            }

    def list_messages(
        self,
        *,
        after_id: Optional[int] = None,
        limit: int = 60,
        coin: str = "ALL",
        side: str = "ALL",
        include_window: bool = False,
        now_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        ts = int(time.time()) if now_ts is None else int(now_ts)
        lim = max(1, min(100, int(limit)))
        clauses = ["hidden = 0"]
        params: List[Any] = []
        if coin in ALLOWED_COINS and coin != "ALL":
            clauses.append("coin = ?")
            params.append(coin)
        if side in ALLOWED_SIDES:
            clauses.append("side = ?")
            params.append(side)

        with self._lock:
            has_more = False
            if after_id is not None:
                after = max(0, min(MAX_SQLITE_INT64, int(after_id)))
                inc_clauses = list(clauses) + ["id > ?"]
                inc_params = list(params) + [after, lim + 1]
                sql = (
                    "SELECT id, created_at, coin, side, nick, text, likes "
                    f"FROM barrage_messages WHERE {' AND '.join(inc_clauses)} "
                    "ORDER BY id ASC LIMIT ?"
                )
                rows = self._conn.execute(sql, inc_params).fetchall()
                if len(rows) > lim:
                    has_more = True
                    rows = rows[:lim]
                next_cursor = int(rows[-1]["id"]) if rows else after
            else:
                snap_params = list(params) + [lim]
                sql = (
                    "SELECT id, created_at, coin, side, nick, text, likes "
                    f"FROM barrage_messages WHERE {' AND '.join(clauses)} "
                    "ORDER BY id DESC LIMIT ?"
                )
                rows = self._conn.execute(sql, snap_params).fetchall()
                next_cursor = int(rows[0]["id"]) if rows else 0

            latest_id = self._latest_id_unlocked()
            stats_24h = self._stats_24h_unlocked(ts)

            resp: Dict[str, Any] = {
                "ok": True,
                "server_time": ts,
                "latest_id": latest_id,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "stats_24h": stats_24h,
                "items": [self._row_to_public(r) for r in rows],
            }

            if include_window:
                win_params = list(params) + [WINDOW_SYNC_LIMIT]
                win_sql = (
                    "SELECT id, created_at, coin, side, nick, text, likes "
                    f"FROM barrage_messages WHERE {' AND '.join(clauses)} "
                    "ORDER BY id DESC LIMIT ?"
                )
                win_rows = self._conn.execute(win_sql, win_params).fetchall()
                resp["window"] = [self._row_to_public(r) for r in win_rows]

            return resp

    def post_message(
        self,
        *,
        client_ip: str,
        nick: Any,
        side: Any,
        coin: Any,
        text: Any,
        now_ts: Optional[int] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        ts = int(time.time()) if now_ts is None else int(now_ts)

        if has_surrogates(nick) or has_surrogates(text):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "文本包含非法字符编码"}

        side_str = str(side if side is not None else "NEUTRAL").strip().upper()
        if side_str not in ALLOWED_SIDES:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "阵营参数无效（仅支持 LONG / SHORT / NEUTRAL）"}

        coin_str = str(coin if coin is not None else "BTC").strip().upper()
        if coin_str not in ALLOWED_COINS:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "币种参数无效"}

        if nick is not None and not isinstance(nick, str):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "昵称格式无效"}
        if not isinstance(text, str):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "弹幕或留言内容不能为空"}

        clean_nick = sanitize_text(nick) if nick is not None else ""
        if not clean_nick:
            clean_nick = DEFAULT_NICK
        if len(clean_nick) > MAX_NICK_LEN:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"昵称最多 {MAX_NICK_LEN} 个字符"}

        clean_text = sanitize_text(text)
        if not clean_text:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "弹幕或留言内容不能为空"}
        if len(clean_text) > MAX_TEXT_LEN:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"单条内容最多 {MAX_TEXT_LEN} 字"}

        spam_err = check_spam(clean_nick, clean_text)
        if spam_err:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": spam_err}

        ip_hash = self.hash_ip(client_ip)
        norm_text = _normalized_for_spam_check(clean_text)

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # 1. Global circuit breaker
                g_row = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM barrage_messages WHERE created_at >= ?",
                    (ts - GLOBAL_WINDOW_SEC,),
                ).fetchone()
                if int(g_row["cnt"]) >= GLOBAL_MAX_POSTS_PER_MIN:
                    self._conn.execute("ROLLBACK")
                    return HTTPStatus.TOO_MANY_REQUESTS, {
                        "ok": False,
                        "error": "当前战场弹幕过于密集，请稍后再试",
                        "retry_after": 5,
                    }

                # 2. Per-IP cooldown (4s)
                last_row = self._conn.execute(
                    "SELECT created_at FROM barrage_messages WHERE ip_hash = ? ORDER BY created_at DESC LIMIT 1",
                    (ip_hash,),
                ).fetchone()
                if last_row and (ts - int(last_row["created_at"])) < COOLDOWN_SEC:
                    wait = max(1, COOLDOWN_SEC - (ts - int(last_row["created_at"])))
                    self._conn.execute("ROLLBACK")
                    return HTTPStatus.TOO_MANY_REQUESTS, {
                        "ok": False,
                        "error": f"发射过快，请 {wait} 秒后再发",
                        "retry_after": wait,
                    }

                # 3. Per-IP 5m burst limit (includes hidden rows to prevent moderation bypass)
                r5m = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM barrage_messages WHERE ip_hash = ? AND created_at >= ?",
                    (ip_hash, ts - WINDOW_5M_SEC),
                ).fetchone()
                if int(r5m["cnt"]) >= MAX_POSTS_5M:
                    self._conn.execute("ROLLBACK")
                    return HTTPStatus.TOO_MANY_REQUESTS, {
                        "ok": False,
                        "error": "5 分钟内发送次数已达上限，请稍歇片刻",
                        "retry_after": 30,
                    }

                # 4. Per-IP 1h limit
                r1h = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM barrage_messages WHERE ip_hash = ? AND created_at >= ?",
                    (ip_hash, ts - WINDOW_1H_SEC),
                ).fetchone()
                if int(r1h["cnt"]) >= MAX_POSTS_1H:
                    self._conn.execute("ROLLBACK")
                    return HTTPStatus.TOO_MANY_REQUESTS, {
                        "ok": False,
                        "error": "本小时发言条数已达上限，请稍后再来",
                        "retry_after": 120,
                    }

                # 5. Per-IP duplicate text guard (60s)
                dup = self._conn.execute(
                    "SELECT 1 FROM barrage_messages WHERE ip_hash = ? AND norm_text = ? AND created_at >= ? LIMIT 1",
                    (ip_hash, norm_text, ts - DUP_GUARD_SEC),
                ).fetchone()
                if dup:
                    self._conn.execute("ROLLBACK")
                    return HTTPStatus.TOO_MANY_REQUESTS, {
                        "ok": False,
                        "error": "请勿短时间内重复刷屏相同内容",
                        "retry_after": 15,
                    }

                cur = self._conn.execute(
                    """
                    INSERT INTO barrage_messages (created_at, coin, side, nick, text, norm_text, ip_hash, likes, hidden)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)
                    """,
                    (ts, coin_str, side_str, clean_nick, clean_text, norm_text, ip_hash),
                )
                msg_id = int(cur.lastrowid)

                self._insert_counter += 1
                if self._insert_counter % 50 == 0:
                    self._prune_unlocked(ts)

                # Compute 24h stats inside the same committed transaction
                stats_24h = self._stats_24h_unlocked(ts)
                self._conn.execute("COMMIT")
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

        return HTTPStatus.CREATED, {
            "ok": True,
            "server_time": ts,
            "stats_24h": stats_24h,
            "item": {
                "id": msg_id,
                "ts": ts,
                "coin": coin_str,
                "side": side_str,
                "nick": clean_nick,
                "text": clean_text,
                "likes": 0,
            },
        }

    def like_message(
        self,
        *,
        client_ip: str,
        msg_id: Any,
        now_ts: Optional[int] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        ts = int(time.time()) if now_ts is None else int(now_ts)
        mid = parse_strict_int(msg_id, min_val=1)
        if mid is None:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "留言 ID 无效"}

        ip_hash = self.hash_ip(client_ip)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cnt_row = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM barrage_likes WHERE ip_hash = ? AND created_at >= ?",
                    (ip_hash, ts - LIKE_WINDOW_SEC),
                ).fetchone()
                if int(cnt_row["cnt"]) >= MAX_LIKES_PER_MIN:
                    self._conn.execute("ROLLBACK")
                    return HTTPStatus.TOO_MANY_REQUESTS, {
                        "ok": False,
                        "error": "点赞过于频繁，请稍后再试",
                        "retry_after": 10,
                    }

                msg_row = self._conn.execute(
                    "SELECT id, likes FROM barrage_messages WHERE id = ? AND hidden = 0",
                    (mid,),
                ).fetchone()
                if not msg_row:
                    self._conn.execute("ROLLBACK")
                    return HTTPStatus.NOT_FOUND, {"ok": False, "error": "留言不存在或已移除"}

                ins = self._conn.execute(
                    "INSERT OR IGNORE INTO barrage_likes (msg_id, ip_hash, created_at) VALUES (?, ?, ?)",
                    (mid, ip_hash, ts),
                )
                already_liked = ins.rowcount == 0
                if not already_liked:
                    self._conn.execute(
                        "UPDATE barrage_messages SET likes = likes + 1 WHERE id = ?",
                        (mid,),
                    )
                updated = self._conn.execute(
                    "SELECT likes FROM barrage_messages WHERE id = ?",
                    (mid,),
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

        return HTTPStatus.OK, {
            "ok": True,
            "id": mid,
            "likes": int(updated["likes"]) if updated else int(msg_row["likes"]),
            "already_liked": already_liked,
        }

    def hide_message(self, msg_id: int) -> bool:
        mid = parse_strict_int(msg_id, min_val=1)
        if mid is None:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE barrage_messages SET hidden = 1 WHERE id = ?",
                (mid,),
            )
            return cur.rowcount > 0

    def _prune_unlocked(self, now_ts: int) -> None:
        """Never prune messages younger than 25h so 24h stats & 1h rate limits stay exact."""
        protect_cutoff = now_ts - RETENTION_PROTECT_SEC
        self._conn.execute(
            """
            DELETE FROM barrage_messages
            WHERE created_at < ?
              AND id NOT IN (
                  SELECT id FROM barrage_messages ORDER BY id DESC LIMIT ?
              )
            """,
            (protect_cutoff, MAX_STORED_MESSAGES),
        )
        self._conn.execute(
            """
            DELETE FROM barrage_likes
            WHERE msg_id NOT IN (SELECT id FROM barrage_messages)
            """
        )


def make_handler(store: BarrageStore, allowed_write_origins: FrozenSet[str]):
    class BarrageHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "DanClubBarrage/1.1"
        sys_version = ""
        timeout = SOCKET_TIMEOUT_SEC

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(f"[barrage] {time.strftime('%Y-%m-%d %H:%M:%S')} {fmt % args}\n")

        def _client_ip(self) -> str:
            peer = self.client_address[0] if self.client_address else "unknown"
            # Trust X-Real-Client-IP ONLY from local reverse proxy (Caddy on loopback)
            if peer in LOOPBACK_ADDRS:
                hdr = (self.headers.get("X-Real-Client-IP") or "").strip()
                if hdr and len(hdr) <= 64:
                    return hdr
            return peer

        def _req_origin(self) -> str:
            return (self.headers.get("Origin") or "").strip()

        def _origin_allowed_for_write(self, origin: str) -> bool:
            if not origin:
                return True  # Non-browser CLI/server calls without Origin still pass rate-limit & JSON checks
            return origin in allowed_write_origins

        def _send_cors_headers(self, *, write_endpoint: bool = False) -> None:
            origin = self._req_origin()
            self.send_header("Vary", "Origin")
            if write_endpoint:
                if origin and origin in allowed_write_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
            else:
                if origin and origin in allowed_write_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                else:
                    self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Cache-Control", "no-store")

        def _send_json(self, status: int, payload: Dict[str, Any], *, write_endpoint: bool = False) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(int(status))
            self._send_cors_headers(write_endpoint=write_endpoint)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self, max_bytes: int) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, str]]]:
            ct = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ct != "application/json":
                return None, (HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type 必须为 application/json")
            cl_hdr = self.headers.get("Content-Length")
            if cl_hdr is None:
                return None, (HTTPStatus.LENGTH_REQUIRED, "缺少 Content-Length")
            try:
                length = int(cl_hdr)
            except ValueError:
                return None, (HTTPStatus.BAD_REQUEST, "Content-Length 无效")
            if length < 0 or length > max_bytes:
                return None, (HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求体过大")

            deadline = time.monotonic() + REQUEST_DEADLINE_SEC
            chunks: List[bytes] = []
            remaining_bytes = length
            while remaining_bytes > 0:
                rem_time = deadline - time.monotonic()
                if rem_time <= 0:
                    return None, (HTTPStatus.REQUEST_TIMEOUT, "请求读取超时")
                self.connection.settimeout(rem_time)
                try:
                    chunk = self.rfile.read1(min(remaining_bytes, 1024))
                except socket.timeout:
                    return None, (HTTPStatus.REQUEST_TIMEOUT, "请求读取超时")
                if time.monotonic() > deadline:
                    return None, (HTTPStatus.REQUEST_TIMEOUT, "请求读取超时")
                if not chunk:
                    return None, (HTTPStatus.BAD_REQUEST, "请求体不完整")
                chunks.append(chunk)
                remaining_bytes -= len(chunk)

            raw = b"".join(chunks)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, (HTTPStatus.BAD_REQUEST, "JSON 格式无效")
            if not isinstance(obj, dict):
                return None, (HTTPStatus.BAD_REQUEST, "请求体必须为 JSON 对象")
            return obj, None

        def do_OPTIONS(self) -> None:
            origin = self._req_origin()
            if origin and not self._origin_allowed_for_write(origin):
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "不允许的跨站来源"}, write_endpoint=True)
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers(write_endpoint=bool(origin))
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                if path == "/api/v1/barrage/health":
                    self._send_json(HTTPStatus.OK, store.health())
                    return
                if path == "/api/v1/barrage":
                    qs = parse_qs(parsed.query)
                    after_id: Optional[int] = None
                    if "after_id" in qs and qs["after_id"]:
                        raw_after = qs["after_id"][0].strip()
                        if raw_after != "":
                            try:
                                parsed_after = int(raw_after)
                            except ValueError:
                                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "after_id 参数无效"})
                                return
                            if parsed_after < 0 or parsed_after > MAX_SQLITE_INT64:
                                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "after_id 范围无效"})
                                return
                            after_id = parsed_after
                    try:
                        limit = int(qs.get("limit", ["60"])[0])
                    except ValueError:
                        limit = 60
                    coin = (qs.get("coin", ["ALL"])[0] or "ALL").strip().upper()
                    side = (qs.get("side", ["ALL"])[0] or "ALL").strip().upper()
                    include_window = qs.get("include_window", ["0"])[0] in ("1", "true", "yes")
                    data = store.list_messages(
                        after_id=after_id,
                        limit=limit,
                        coin=coin,
                        side=side,
                        include_window=include_window,
                    )
                    self._send_json(HTTPStatus.OK, data)
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
            except Exception:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "服务端异常"})

        def do_POST(self) -> None:
            try:
                origin = self._req_origin()
                if not self._origin_allowed_for_write(origin):
                    self._send_json(
                        HTTPStatus.FORBIDDEN,
                        {"ok": False, "error": "仅允许从 DanClub 官方终端提交弹幕或点赞"},
                        write_endpoint=True,
                    )
                    return

                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                client_ip = self._client_ip()
                if path == "/api/v1/barrage":
                    body, err = self._read_json_body(MAX_POST_BYTES)
                    if err:
                        self._send_json(err[0], {"ok": False, "error": err[1]}, write_endpoint=True)
                        return
                    assert body is not None
                    status, res = store.post_message(
                        client_ip=client_ip,
                        nick=body.get("nick"),
                        side=body.get("side"),
                        coin=body.get("coin"),
                        text=body.get("text"),
                    )
                    self._send_json(status, res, write_endpoint=True)
                    return

                if path == "/api/v1/barrage/like":
                    body, err = self._read_json_body(MAX_LIKE_BYTES)
                    if err:
                        self._send_json(err[0], {"ok": False, "error": err[1]}, write_endpoint=True)
                        return
                    assert body is not None
                    status, res = store.like_message(
                        client_ip=client_ip,
                        msg_id=body.get("id"),
                    )
                    self._send_json(status, res, write_endpoint=True)
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"}, write_endpoint=True)
            except Exception:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "服务端异常"}, write_endpoint=True)

    return BarrageHandler


class HardenedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with pre-thread bounded admission control."""

    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, server_address, RequestHandlerClass, max_concurrent: int = MAX_CONCURRENT_REQUESTS) -> None:
        self._conn_gate = threading.BoundedSemaphore(max_concurrent)
        super().__init__(server_address, RequestHandlerClass)

    def process_request(self, request, client_address) -> None:
        if not self._conn_gate.acquire(blocking=False):
            try:
                request.settimeout(1.0)
                request.sendall(_BUSY_503_RESP)
            except Exception:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._conn_gate.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._conn_gate.release()


def create_server(
    host: str,
    port: int,
    store: BarrageStore,
    *,
    allowed_write_origins: FrozenSet[str] = DEFAULT_WRITE_ORIGINS,
    max_concurrent: int = MAX_CONCURRENT_REQUESTS,
) -> HardenedThreadingHTTPServer:
    handler_cls = make_handler(store, allowed_write_origins)
    return HardenedThreadingHTTPServer((host, port), handler_cls, max_concurrent=max_concurrent)


def main() -> int:
    parser = argparse.ArgumentParser(description="DanClub Live Danmaku & Message Board Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=18820, help="Bind port (default: 18820)")
    parser.add_argument(
        "--db",
        default="/var/lib/danclub-barrage/barrage.db",
        help="SQLite database path",
    )
    parser.add_argument(
        "--secret-file",
        default="/var/lib/danclub-barrage/hmac.secret",
        help="Persistent 32-byte HMAC secret path",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="Additional allowed browser Origin for POST writes",
    )
    parser.add_argument("--hide-id", type=int, default=0, help="Admin: soft-hide message ID and exit")
    parser.add_argument("--stats", action="store_true", help="Print health stats JSON and exit")
    args = parser.parse_args()

    secret = load_or_create_secret(Path(args.secret_file))
    store = BarrageStore(Path(args.db), secret)

    if args.hide_id > 0:
        ok = store.hide_message(args.hide_id)
        print(json.dumps({"ok": ok, "hidden_id": args.hide_id}))
        store.close()
        return 0 if ok else 1

    if args.stats:
        print(json.dumps(store.health(), ensure_ascii=False))
        store.close()
        return 0

    origins = frozenset(set(DEFAULT_WRITE_ORIGINS) | {o.strip() for o in args.allow_origin if o.strip()})
    server = create_server(args.host, args.port, store, allowed_write_origins=origins)

    def _shutdown(signum: int, _frame: Any) -> None:
        sys.stderr.write(f"[barrage] received signal {signum}, shutting down...\n")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    sys.stderr.write(f"[barrage] listening on http://{args.host}:{args.port} (db={args.db})\n")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
