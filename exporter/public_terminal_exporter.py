#!/usr/bin/env python3
"""Read-only whitelisted public market data exporter for DanClub Open Terminal.

Security guarantees:
- Opens ONLY explicitly whitelisted public market SQLite databases in strict read-only URI mode.
- NEVER accesses private chat logs, personal holdings, credentials, or audit logs.
- Enforces the shared privacy_guard fail-closed check before writing any --out-dir files.
- Contains ZERO deployment-specific literals (IPs, hostnames, emails, account IDs).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parent
for _cand in (_HERE, _HERE.parent / "scripts"):
    if (_cand / "privacy_guard.py").is_file() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

try:
    import privacy_guard as _pg
except ImportError:
    _pg = None

BJT = timezone(timedelta(hours=8))
US_ET = ZoneInfo("America/New_York")
DEFAULT_DATA_DIR = Path(os.environ.get("DANCLUB_DATA_DIR", str(Path(__file__).resolve().parent / "data")))

BOTTOM_KEYS: list[tuple[str, str]] = [
    ("btc_low", "BTC 守住前低"),
    ("etf_flow", "现货 ETF 流向"),
    ("oi_funding", "合约杠杆与费率"),
    ("stables", "USDT + USDC 双龙头供给"),
    ("us10y", "美债 10Y 收益率"),
    ("cb_premium", "Coinbase 溢价"),
    ("crypto_stocks", "币股联动情绪"),
]

MAJOR_WALLET_COINS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
    "TRX", "TON", "SUI", "UNI", "LTC", "BCH", "NEAR", "APT", "ARB", "OP",
    "ATOM", "FIL", "AAVE", "HYPE", "USDT", "USDC", "FDUSD", "USDE", "DAI",
    "PEPE", "SHIB", "WIF", "TAO", "RENDER", "FET", "ENA", "ONDO", "MNT",
}

MAJOR_OI_ANCHORS = {
    "BTC", "ETH", "SOL", "BNB", "HYPE", "XRP", "DOGE", "ADA", "AVAX", "ZEC",
    "LINK", "SUI", "TAO", "1000PEPE", "AAVE", "UNI", "LTC", "BCH",
}

MACRO_WATCHLIST: list[tuple[str, str, str, bool]] = [
    # (symbol, zh_name, group, is_24h_market)
    ("SPY", "标普500 ETF", "美股指数", False),
    ("QQQ", "纳指100 ETF", "美股指数", False),
    (".VIX", "恐慌指数 VIX", "波动率", False),
    ("US10Y", "美债 10Y", "利率", True),
    (".DXY", "美元指数", "外汇", True),
    ("JPY=", "美元/日元", "外汇", True),
    ("CNH=", "离岸人民币", "外汇", True),
    ("XAU=", "现货黄金", "贵金属", True),
    ("XAG=", "现货白银", "贵金属", True),
    ("NVDA", "英伟达", "算力存储", False),
    ("MU", "美光科技", "算力存储", False),
    ("SNDK", "闪迪 SanDisk", "算力存储", False),
    ("MSTR", "Strategy", "币股", False),
    ("COIN", "Coinbase", "币股", False),
    ("CRCL", "Circle", "币股", False),
    ("HOOD", "Robinhood", "币股", False),
]

ALLOWED_PROMO_DOMAINS: tuple[str, ...] = (
    "binance.com",
    "okx.com",
    "bybit.com",
    "bitget.com",
    "gate.io",
    "gate.com",
    "kraken.com",
    "coinbase.com",
    "hyperliquid.xyz",
)

GENERIC_PRIVATE_RE = re.compile(r"(?:微信私聊|私聊主人|微信群|个人持仓|我的仓位|早报草稿|@重大事件)", re.IGNORECASE)
ASSET_KEY_RE = re.compile(r"^[A-Z0-9._-]{1,16}$")
WALLET_STALE_SEC = 6 * 3600
CNBC_STALE_SEC = 6 * 3600

# Generic regexes to clean chat-bot prefixes / internal scores / relative times / emojis without hardcoding host IDs
_EMOJI_STRIP_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\u2300-\u23FF\u2600-\u27BF\uFE0F\u200D]+"
)
_NODE_PREFIX_RE = re.compile(
    r"^(?:[A-Z][A-Z0-9_-]{0,5}\s*[·•]\s*)?(?:X快讯|X|Phoenix|英文快讯(?:雷达)?|博客快讯)\s*[·•/]?\s*",
    re.IGNORECASE,
)
_SCORE_RE = re.compile(r"\s*[·•|]\s*\d{2,3}\s*分")
_REL_TIME_RE = re.compile(r"\s*[·•]\s*\d+\s*(?:秒|分钟|小时)前")
_BRACKET_TAG_RE = re.compile(r"【[^】]*】")
_DIVIDER_RE = re.compile(r"[━─═—]{4,}")
_URL_IN_TEXT_RE = re.compile(r"https?://\S+")
_EXCH_NORM = {
    "币安 binance": "Binance",
    "binance": "Binance",
    "欧易 okx": "OKX",
    "okx": "OKX",
    "bybit": "Bybit",
    "bitget": "Bitget",
    "gate": "Gate",
    "gate.io": "Gate",
    "upbit": "Upbit",
    "kraken": "Kraken",
    "coinbase": "Coinbase",
}


def _num(val: Any, ndigits: int = 4) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, ndigits)


def _int_or_zero(val: Any) -> int:
    if val is None or isinstance(val, bool):
        return 0
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError):
        return 0


def _clean_cut(text: str, max_len: int) -> str:
    """Truncate text cleanly at sentence/clause boundary instead of slicing mid-word or mid-URL."""
    s = re.sub(r"\s+", " ", (text or "")).strip()
    if len(s) <= max_len:
        return s
    cut = s[: max_len - 1]
    for sep in ("。", "；", "！", "？", "｜", " | ", "，", ", ", " "):
        idx = cut.rfind(sep)
        if idx >= int(max_len * 0.58):
            return cut[:idx].rstrip("，,；;｜| -") + "…"
    return cut.rstrip("，,；;｜| -") + "…"


def _strip_emojis(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_STRIP_RE.sub("", text or "")).strip()


def _normalize_exchange(raw: str) -> str:
    s = _strip_emojis(raw).strip()
    return _EXCH_NORM.get(s.lower(), s[:24] or "Exchange")


def _clean_promo_url(url: Any) -> str:
    if _pg is not None:
        return _pg.sanitize_public_url(url, allowed_domains=ALLOWED_PROMO_DOMAINS)
    if not isinstance(url, str):
        return ""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return ""
    if p.scheme != "https" or not p.netloc or p.username is not None or p.password is not None or "@" in p.netloc:
        return ""
    host = (p.hostname or "").lower().rstrip(".")
    if not any(host == d or host.endswith("." + d) for d in ALLOWED_PROMO_DOMAINS):
        return ""
    return p._replace(query="", params="", fragment="").geturl()


def _connect_ro(data_dir: Path, filename: str) -> sqlite3.Connection | None:
    db_path = data_dir / filename
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn
    except sqlite3.Error:
        return None


def _safe_query(conn: sqlite3.Connection | None, sql: str, params: tuple = ()) -> tuple[list[dict[str, Any]], bool]:
    if conn is None:
        return [], False
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()], True
    except sqlite3.Error:
        return [], False


def _get_kv(conn: sqlite3.Connection | None, table: str, key: str) -> Any:
    rows, ok = _safe_query(conn, f"SELECT v FROM {table} WHERE k = ?", (key,))
    if not ok or not rows:
        return None
    raw = rows[0].get("v")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> int:
    first_wd = datetime(year, month, 1).weekday()
    return 1 + (weekday - first_wd) % 7 + (nth - 1) * 7


def _last_weekday_of_month(year: int, month: int, weekday: int) -> int:
    nxt = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    last_day = (nxt - timedelta(days=1)).day
    last_wd = datetime(year, month, last_day).weekday()
    return last_day - (last_wd - weekday) % 7


def _good_friday_date(year: int) -> tuple[int, int]:
    """Anonymous Gregorian algorithm for Easter Sunday minus 2 days (Good Friday)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    gf = datetime(year, month, day) - timedelta(days=2)
    return gf.month, gf.day


def _is_us_equity_holiday(et_dt: datetime) -> bool:
    """Detect all NYSE/NASDAQ market holidays (observed + Good Friday) to prevent false LIVE tags."""
    y, m, d = et_dt.year, et_dt.month, et_dt.day
    if (m, d) == _good_friday_date(y):
        return True
    fixed_obs = set()
    for fm, fd in ((1, 1), (6, 19), (7, 4), (12, 25)):
        dt = datetime(y, fm, fd)
        if dt.weekday() == 5:
            dt -= timedelta(days=1)
        elif dt.weekday() == 6:
            dt += timedelta(days=1)
        fixed_obs.add((dt.month, dt.day))
    if (m, d) in fixed_obs:
        return True
    if m == 1 and d == _nth_weekday_of_month(y, 1, 0, 3):  # MLK Day
        return True
    if m == 2 and d == _nth_weekday_of_month(y, 2, 0, 3):  # Presidents' Day
        return True
    if m == 5 and d == _last_weekday_of_month(y, 5, 0):    # Memorial Day
        return True
    if m == 9 and d == _nth_weekday_of_month(y, 9, 0, 1):  # Labor Day
        return True
    if m == 11 and d == _nth_weekday_of_month(y, 11, 3, 4):  # Thanksgiving (4th Thu of Nov)
        return True
    return False


def _is_fx_metals_open(now_ts: int) -> bool:
    """FX/Metals/Treasury futures trade Sun 18:00 ET through Fri 17:00 ET."""
    et = datetime.fromtimestamp(now_ts, tz=US_ET)
    wd = et.weekday()  # Mon=0 .. Sun=6
    if wd == 5:
        return False
    if wd == 6 and et.hour < 18:
        return False
    if wd == 4 and et.hour >= 17:
        return False
    return True


_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _reg_date_matches_today(reg_date_str: str, et_dt: datetime) -> bool:
    """Verify CNBC reg_date matches today ET; FAIL-CLOSED (False) if non-empty date cannot be parsed."""
    s = (reg_date_str or "").strip()
    if not s:
        return True
    m1 = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m1:
        return (int(m1.group(1)), int(m1.group(2)), int(m1.group(3))) == (et_dt.year, et_dt.month, et_dt.day)
    m2 = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", s)
    if m2:
        mo, dy = int(m2.group(1)), int(m2.group(2))
        yr_s = m2.group(3)
        if yr_s:
            yr = int(yr_s) + (2000 if len(yr_s) == 2 else 0)
            return (yr, mo, dy) == (et_dt.year, et_dt.month, et_dt.day)
        return (mo, dy) == (et_dt.month, et_dt.day)
    m3 = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:,?\s*(\d{4}))?", s, re.I)
    if m3:
        mo = _MONTH_ABBR[m3.group(1).lower()[:3]]
        dy = int(m3.group(2))
        yr = int(m3.group(3)) if m3.group(3) else et_dt.year
        return (yr, mo, dy) == (et_dt.year, et_dt.month, et_dt.day)
    # Time-only CNBC string like "10:42 AM EST" implies today's active quote
    if re.search(r"\d{1,2}:\d{2}\s*(?:AM|PM)\s*E[SD]T", s, re.I):
        return True
    return False


def _us_equity_session(now_ts: int, raw_status: str, reg_date: str = "") -> tuple[str, bool]:
    """Fail-closed US equity session detector using NYSE calendar + NY wall-clock + explicit CNBC status."""
    et = datetime.fromtimestamp(now_ts, tz=US_ET)
    st = (raw_status or "").upper()
    if et.weekday() >= 5 or _is_us_equity_holiday(et):
        return "CLOSED", True
    hm = et.hour * 60 + et.minute
    if 9 * 60 + 30 <= hm < 16 * 60:
        # Require EXPLICIT REG_MKT status AND fresh session date (never treat empty status as LIVE!)
        if st == "REG_MKT" and _reg_date_matches_today(reg_date, et):
            return "LIVE", False
        return "CLOSED", True
    if 4 * 60 <= hm < 9 * 60 + 30 and st == "PRE_MKT":
        return "PRE", True
    if 16 * 60 <= hm < 20 * 60 and st == "POST_MKT":
        return "POST", True
    return "CLOSED", True


def export_overview(data_dir: Path, *, now_sec: int | None = None) -> dict[str, Any]:
    now_ts = int(time.time()) if now_sec is None else now_sec
    conn = _connect_ro(data_dir, "macro_market.db")
    try:
        dash_rows, _ = _safe_query(conn, "SELECT item_key, status, detail, updated_at FROM dashboard_state")
        dash_map = {r["item_key"]: r for r in dash_rows}
        bottom_items = []
        green_count = 0
        yellow_count = 0
        red_count = 0
        latest_dash_ts = 0
        for key, label in BOTTOM_KEYS:
            row = dash_map.get(key, {})
            status = str(row.get("status") or "UNKNOWN").upper()
            if status == "GREEN":
                green_count += 1
            elif status == "YELLOW":
                yellow_count += 1
            elif status == "RED":
                red_count += 1
            ts = _int_or_zero(row.get("updated_at"))
            latest_dash_ts = max(latest_dash_ts, ts)
            detail = str(row.get("detail") or "")[:240]
            if key == "stables" and detail and "USDT" not in detail and "双龙头" not in detail:
                detail = detail.replace("总计", "USDT+USDC 合计", 1)
            bottom_items.append({
                "key": key,
                "label": label,
                "status": status,
                "detail": detail,
                "updated_at": ts,
            })

        fng = _get_kv(conn, "kv_state", "fng_cache")
        fng_obj = None
        if isinstance(fng, list) and len(fng) >= 2:
            fng_obj = {"value": _num(fng[0], 0), "label": str(fng[1])}
        elif isinstance(fng, dict):
            fng_obj = {"value": _num(fng.get("value"), 0), "label": str(fng.get("label") or "")}

        stables = _get_kv(conn, "kv_state", "stable_cache")
        stables_obj = None
        if isinstance(stables, dict):
            stables_obj = {
                "scope": "DeFiLlama 全口径稳定币",
                "total_b": _num(stables.get("total_b"), 2),
                "chg_1d_b": _num(stables.get("chg_1d_b"), 2),
                "chg_7d_b": _num(stables.get("chg_7d_b"), 2),
                "chg_30d_b": _num(stables.get("chg_30d_b"), 2),
            }

        tga = _get_kv(conn, "kv_state", "tga_cache")
        tga_obj = None
        if isinstance(tga, dict) and _num(tga.get("bal_b"), 1) is not None:
            tga_obj = {
                "date": str(tga.get("date") or "")[:16],
                "bal_b": _num(tga.get("bal_b"), 1),
                "chg_5d_b": _num(tga.get("chg_5d_b"), 1),
            }

        miner = _get_kv(conn, "kv_state", "btc_miner_metrics")
        miner_obj = None
        if isinstance(miner, dict):
            miner_obj = {
                "ts": _int_or_zero(miner.get("ts")),
                "elec_usd_kwh": _num(miner.get("elec_usd_kwh"), 3),
                "hashrate_eh": _num(miner.get("hashrate_eh"), 1),
                "diff_chg_pct": _num(miner.get("diff_chg_pct"), 2),
                "remaining_blocks": max(0, _int_or_zero(miner.get("remaining_blocks"))),
                "progress_pct": _num(miner.get("progress_pct"), 1),
                "s21pro_usd": _num(miner.get("s21pro_usd"), 0),
                "s21_usd": _num(miner.get("s21_usd"), 0),
                "s19k_usd": _num(miner.get("s19k_usd"), 0),
            }

        qdii = _get_kv(conn, "kv_state", "qdii_premium_snapshot")
        qdii_items = []
        qdii_ts = 0
        if isinstance(qdii, dict) and isinstance(qdii.get("items"), list):
            qdii_ts = _int_or_zero(qdii.get("ts"))
            for it in qdii["items"][:12]:
                if not isinstance(it, dict):
                    continue
                qdii_items.append({
                    "code": str(it.get("code") or "")[:16],
                    "name": str(it.get("name") or "")[:40],
                    "px": _num(it.get("px"), 4),
                    "iopv": _num(it.get("iopv"), 4),
                    "chg_pct": _num(it.get("chg_pct"), 2),
                    "prem_pct": _num(it.get("prem_pct"), 2),
                })
            qdii_items.sort(key=lambda x: x["prem_pct"] if x["prem_pct"] is not None else -999.0, reverse=True)

        # Load rich CNBC latest quotes + 52w drawdown snapshot + fallback reg_chg table
        cnbc_snap = _get_kv(conn, "kv_state", "cnbc_quotes_latest")
        cnbc_quotes: dict[str, dict[str, Any]] = {}
        cnbc_ts = 0
        if isinstance(cnbc_snap, dict) and isinstance(cnbc_snap.get("quotes"), dict):
            cnbc_ts = _int_or_zero(cnbc_snap.get("ts"))
            if cnbc_ts > 0 and (now_ts - cnbc_ts) <= CNBC_STALE_SEC:
                cnbc_quotes = cnbc_snap["quotes"]

        dd_snap = _get_kv(conn, "kv_state", "drawdown_valuation_snapshot")
        dd_map = dd_snap.get("drawdowns", {}) if isinstance(dd_snap, dict) and isinstance(dd_snap.get("drawdowns"), dict) else {}

        reg_rows, _ = _safe_query(conn, "SELECT sym, reg_chg, updated_at FROM stock_reg_cache")
        reg_cache = {str(r["sym"]): _num(r.get("reg_chg"), 2) for r in reg_rows if r.get("sym")}

        macro_quotes = []
        for sym, name, group, is_24h in MACRO_WATCHLIST:
            q_live = cnbc_quotes.get(sym) if isinstance(cnbc_quotes.get(sym), dict) else None
            dd_info = dd_map.get(sym) if isinstance(dd_map.get(sym), dict) else {}
            dd_pct = _num(dd_info.get("dd_pct"), 2)
            yrhi = _num(dd_info.get("yrhi"), 2)

            if q_live and _num(q_live.get("reg_px"), 4) is not None:
                reg_px = _num(q_live.get("reg_px"), 4)
                reg_chg = _num(q_live.get("reg_chg"), 2)
                ext_px = _num(q_live.get("ext_px"), 4)
                ext_chg = _num(q_live.get("ext_chg"), 2)
                raw_status = str(q_live.get("mkt_status") or "").upper()
                reg_date = str(q_live.get("reg_date") or "")
                if is_24h:
                    fx_open = _is_fx_metals_open(now_ts)
                    session = "LIVE" if fx_open else "CLOSED"
                    is_closed = not fx_open
                else:
                    session, is_closed = _us_equity_session(now_ts, raw_status, reg_date)
                # CBOE resets .VIX change_pct to 0.0 outside trading session; hide misleading 0.00%
                if sym == ".VIX" and is_closed and reg_chg == 0.0:
                    reg_chg = None

                macro_quotes.append({
                    "symbol": sym,
                    "name": name,
                    "group": group,
                    "price": reg_px,
                    "chg_pct": reg_chg,
                    "ext_px": ext_px if (not is_24h and session in ("PRE", "POST")) else None,
                    "ext_chg": ext_chg if (not is_24h and session in ("PRE", "POST")) else None,
                    "session": session,
                    "is_closed": is_closed,
                    "dd_pct": dd_pct,
                    "yrhi": yrhi,
                    "ts": cnbc_ts,
                })
                continue

            # Graceful fallback when cnbc_quotes_latest is absent: NEVER compute chg_pct from arbitrary oldest history!
            latest, ok_l = _safe_query(
                conn,
                "SELECT ts_sec, price FROM quotes_60m WHERE symbol = ? ORDER BY ts_sec DESC LIMIT 1",
                (sym,),
            )
            if not ok_l or not latest:
                continue
            ts_now = _int_or_zero(latest[0].get("ts_sec"))
            px_now = _num(latest[0].get("price"), 4)
            if px_now is None:
                continue
            chg_pct = reg_cache.get(sym)
            if chg_pct is None and is_24h and ts_now > 0:
                # Only compute 24h change when a true ~24h-ago reference point exists (within 23h..25h window)
                prev, _ = _safe_query(
                    conn,
                    "SELECT price FROM quotes_60m WHERE symbol = ? AND ts_sec BETWEEN ? AND ? ORDER BY ABS(ts_sec - ?) ASC LIMIT 1",
                    (sym, ts_now - 90000, ts_now - 82800, ts_now - 86400),
                )
                if prev:
                    p0 = _num(prev[0].get("price"), 8)
                    if p0 is not None and p0 > 0:
                        chg_pct = _num((px_now / p0 - 1.0) * 100.0, 2)

            if is_24h:
                fx_live = _is_fx_metals_open(now_ts) and ts_now > 0 and (now_ts - ts_now) <= 3600
                fb_session = "LIVE" if fx_live else "CLOSED"
                fb_closed = not fx_live
            else:
                fb_session, fb_closed = "CLOSED", True
            if sym == ".VIX" and fb_closed and chg_pct == 0.0:
                chg_pct = None

            reg_fallback_px = _num(dd_info.get("px"), 4) if (not is_24h and dd_info) else None
            macro_quotes.append({
                "symbol": sym,
                "name": name,
                "group": group,
                "price": reg_fallback_px if reg_fallback_px is not None else px_now,
                "chg_pct": chg_pct,
                "ext_px": None,
                "ext_chg": None,
                "session": fb_session,
                "is_closed": fb_closed,
                "dd_pct": dd_pct,
                "yrhi": yrhi,
                "ts": ts_now,
            })

        us10y_rows, _ = _safe_query(
            conn,
            "SELECT date_bjt, yield_pct FROM us10y_history ORDER BY date_bjt DESC LIMIT 14",
        )
        us10y_hist = [
            {"date": str(r["date_bjt"]), "yield_pct": _num(r["yield_pct"], 3)}
            for r in reversed(us10y_rows)
        ]

        return {
            "bottom_dashboard": {
                "green": green_count,
                "yellow": yellow_count,
                "red": red_count,
                "total": len(BOTTOM_KEYS),
                "resonance_ready": green_count >= 5,
                "updated_at": latest_dash_ts,
                "items": bottom_items,
            },
            "fear_greed": fng_obj,
            "stablecoins": stables_obj,
            "tga": tga_obj,
            "btc_miner": miner_obj,
            "qdii_updated_at": qdii_ts,
            "qdii_premium": qdii_items,
            "macro_quotes": macro_quotes,
            "us10y_history": us10y_hist,
            "market_regime": None,
            "desk_signals": [],
        }
    finally:
        if conn:
            conn.close()


def export_arb(data_dir: Path, *, now_sec: int | None = None) -> dict[str, Any]:
    now_ts = int(time.time()) if now_sec is None else now_sec
    spreads: dict[str, Any] = {}
    conn_sp = _connect_ro(data_dir, "spread_arb.db")
    try:
        pair_meta = {
            "SKHY_VS_SKHYNIX": {
                "title": "海力士美股 ADR×10 vs 韩股合约",
                "leg_a": "SKHYNIXUSDT (韩股本股)",
                "leg_b": "SKHYUSDT × 10 (美股ADR)",
                "unit": "%",
            },
            "XAU_VS_XAUT": {
                "title": "现货黄金 XAU vs 链上黄金 XAUT",
                "leg_a": "XAUUSDT",
                "leg_b": "XAUTUSDT",
                "unit": "%",
            },
            "XAU_VS_PAXG": {
                "title": "现货黄金 XAU vs 链上黄金 PAXG",
                "leg_a": "XAUUSDT",
                "leg_b": "PAXGUSDT",
                "unit": "%",
            },
        }
        for pid, meta in pair_meta.items():
            latest, _ = _safe_query(
                conn_sp,
                "SELECT ts, px_a, px_b_raw, px_b_adj, spread_pct, spread_usd FROM spread_ticks WHERE pair_id = ? ORDER BY ts DESC LIMIT 1",
                (pid,),
            )
            daily_desc, _ = _safe_query(
                conn_sp,
                "SELECT date_bjt, samples, last_px_a, last_px_b_raw, last_px_b_adj, last_spread_pct, min_spread_pct, max_spread_pct, sum_spread_pct FROM spread_daily WHERE pair_id = ? ORDER BY date_bjt DESC LIMIT 14",
                (pid,),
            )
            daily = list(reversed(daily_desc))

            raw_24h, _ = _safe_query(
                conn_sp,
                "SELECT ts, px_a, px_b_adj, spread_pct FROM spread_ticks WHERE pair_id = ? AND ts >= ? ORDER BY ts ASC",
                (pid, now_ts - 86400),
            )
            buckets: dict[int, dict[str, Any]] = {}
            for r in raw_24h:
                ts_v = _int_or_zero(r.get("ts"))
                sp_v = _num(r.get("spread_pct"), 3)
                if ts_v <= 0 or sp_v is None:
                    continue
                b = ts_v // 900 * 900
                buckets[b] = {
                    "ts": ts_v,
                    "px_a": _num(r.get("px_a"), 2),
                    "px_b_adj": _num(r.get("px_b_adj"), 2),
                    "spread_pct": sp_v,
                }
            series_24h = [buckets[k] for k in sorted(buckets.keys())][-96:]

            stats_72h_rows, _ = _safe_query(
                conn_sp,
                "SELECT spread_pct FROM spread_ticks WHERE pair_id = ? AND ts >= ? ORDER BY ts ASC",
                (pid, now_ts - 72 * 3600),
            )
            vals = [v for r in stats_72h_rows if (v := _num(r.get("spread_pct"), 6)) is not None]
            lat = latest[0] if latest else None
            lat_sp = _num(lat.get("spread_pct"), 6) if lat else None
            band_72h = None
            if len(vals) >= 10:
                mean = sum(vals) / len(vals)
                var = sum((x - mean) ** 2 for x in vals) / len(vals)
                std = math.sqrt(var)
                ref_val = lat_sp if lat_sp is not None else vals[-1]
                z = (ref_val - mean) / std if std > 1e-9 else 0.0
                band_72h = {
                    "mean": _num(mean, 3),
                    "std": _num(std, 3),
                    "min": _num(min(vals), 3),
                    "max": _num(max(vals), 3),
                    "zscore": _num(z, 2),
                }

            daily_clean = []
            for d in daily:
                samples = _int_or_zero(d.get("samples"))
                sum_sp = _num(d.get("sum_spread_pct"), 6)
                last_sp = _num(d.get("last_spread_pct"), 3)
                avg_sp = _num(sum_sp / samples, 3) if (samples > 0 and sum_sp is not None) else last_sp
                daily_clean.append({
                    "date": str(d.get("date_bjt") or ""),
                    "last": last_sp,
                    "min": _num(d.get("min_spread_pct"), 3),
                    "max": _num(d.get("max_spread_pct"), 3),
                    "avg": avg_sp,
                })

            spreads[pid] = {
                **meta,
                "latest": {
                    "ts": _int_or_zero(lat.get("ts")),
                    "px_a": _num(lat.get("px_a"), 2),
                    "px_b_raw": _num(lat.get("px_b_raw"), 3),
                    "px_b_adj": _num(lat.get("px_b_adj"), 2),
                    "spread_pct": _num(lat.get("spread_pct"), 3),
                    "spread_usd": _num(lat.get("spread_usd"), 2),
                } if lat else None,
                "band_72h": band_72h,
                "series_24h": series_24h,
                "daily_14d": daily_clean,
            }
    finally:
        if conn_sp:
            conn_sp.close()

    csi_contracts = []
    conn_csi = _connect_ro(data_dir, "csi_basis.db")
    try:
        rows, _ = _safe_query(
            conn_csi,
            """
            SELECT quote_date, quote_time, code, contract, expiry, days_left, spot, fut, basis_pts, basis_pct, ann_pct, oi, vol
            FROM basis_snapshots
            WHERE id IN (SELECT MAX(id) FROM basis_snapshots GROUP BY contract)
              AND days_left > 0
            ORDER BY code ASC, days_left ASC
            """,
        )
        for r in rows:
            csi_contracts.append({
                "quote_date": str(r.get("quote_date") or ""),
                "quote_time": str(r.get("quote_time") or ""),
                "code": str(r.get("code") or ""),
                "contract": str(r.get("contract") or ""),
                "expiry": str(r.get("expiry") or ""),
                "days_left": max(0, _int_or_zero(r.get("days_left"))),
                "spot": _num(r.get("spot"), 2),
                "fut": _num(r.get("fut"), 2),
                "basis_pts": _num(r.get("basis_pts"), 2),
                "basis_pct": _num(r.get("basis_pct"), 2),
                "ann_pct": _num(r.get("ann_pct"), 2),
                "oi": max(0, _int_or_zero(r.get("oi"))),
                "vol": max(0, _int_or_zero(r.get("vol"))),
            })
    finally:
        if conn_csi:
            conn_csi.close()

    wallet_summary = []
    wallet_major_halts = []
    wallet_ok = False
    wallet_updated_at = 0
    wallet_reason = "数据源暂不可用"
    conn_opp = _connect_ro(data_dir, "opportunity.db")
    try:
        sum_rows, ok_sum = _safe_query(
            conn_opp,
            """
            SELECT ex,
                   COUNT(*) AS total,
                   SUM(CASE WHEN dep_ok = 0 AND wd_ok = 0 THEN 1 ELSE 0 END) AS both_halted,
                   SUM(CASE WHEN dep_ok = 0 AND wd_ok = 1 THEN 1 ELSE 0 END) AS dep_only_halted,
                   SUM(CASE WHEN dep_ok = 1 AND wd_ok = 0 THEN 1 ELSE 0 END) AS wd_only_halted,
                   MAX(updated_at) AS updated_at
            FROM wallet_halt_state
            GROUP BY ex
            ORDER BY ex ASC
            """,
        )
        placeholders = ",".join("?" for _ in MAJOR_WALLET_COINS)
        halt_rows, ok_halt = _safe_query(
            conn_opp,
            f"""
            SELECT ex, coin, dep_ok, wd_ok, halted_since, updated_at
            FROM wallet_halt_state
            WHERE coin IN ({placeholders})
              AND (dep_ok = 0 OR wd_ok = 0)
            ORDER BY coin ASC, ex ASC
            """,
            tuple(sorted(MAJOR_WALLET_COINS)),
        )
        if ok_sum and ok_halt:
            for r in sum_rows:
                upd = _int_or_zero(r.get("updated_at"))
                wallet_updated_at = max(wallet_updated_at, upd)
                wallet_summary.append({
                    "exchange": str(r.get("ex") or ""),
                    "total_coins": max(0, _int_or_zero(r.get("total"))),
                    "both_halted": max(0, _int_or_zero(r.get("both_halted"))),
                    "dep_only_halted": max(0, _int_or_zero(r.get("dep_only_halted"))),
                    "wd_only_halted": max(0, _int_or_zero(r.get("wd_only_halted"))),
                    "updated_at": upd,
                })
            for r in halt_rows:
                wallet_major_halts.append({
                    "exchange": str(r.get("ex") or ""),
                    "coin": str(r.get("coin") or ""),
                    "dep_ok": bool(r.get("dep_ok")),
                    "wd_ok": bool(r.get("wd_ok")),
                    "halted_since": _int_or_zero(r.get("halted_since")),
                    "updated_at": _int_or_zero(r.get("updated_at")),
                })
            fresh_exchanges = sum(
                1 for w in wallet_summary if w["updated_at"] > 0 and (now_ts - w["updated_at"]) <= WALLET_STALE_SEC
            )
            if fresh_exchanges >= 4:
                wallet_ok = True
                wallet_reason = "ok"
            elif len(wallet_summary) < 4:
                wallet_reason = f"仅覆盖 {len(wallet_summary)}/4 家交易所，状态不完整"
            else:
                wallet_reason = f"仅 {fresh_exchanges}/4 家交易所在 6 小时内有新鲜快照，状态未知"
    finally:
        if conn_opp:
            conn_opp.close()

    return {
        "spreads": spreads,
        "csi_basis": csi_contracts,
        "wallet_status": {
            "ok": wallet_ok,
            "exchanges_covered": len(wallet_summary),
            "updated_at": wallet_updated_at,
            "reason": wallet_reason,
            "summary": wallet_summary,
            "major_halts": wallet_major_halts,
        },
    }


def _parse_etf_detail(detail: str | None) -> list[dict[str, Any]]:
    if not detail:
        return []
    out = []
    for part in str(detail).split("|"):
        tok = part.strip()
        m = re.match(r"^([A-Z0-9]+)\s+([+-]?\d+(?:\.\d+)?)$", tok)
        if m:
            out.append({"ticker": m.group(1), "musd": _num(m.group(2), 1)})
    return out


def _compute_etf_streak(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Count consecutive latest trading-day inflows/outflows; any zero or missing day breaks the streak."""
    if not items:
        return None
    last_val = items[-1].get("total_musd")
    if last_val is None or last_val == 0:
        return None
    last_sign = 1 if last_val > 0 else -1
    days = 0
    for x in reversed(items):
        v = x.get("total_musd")
        if v is None or v == 0:
            break
        if (1 if v > 0 else -1) == last_sign:
            days += 1
        else:
            break
    return {"dir": "inflow" if last_sign > 0 else "outflow", "days": days}


def export_flows(data_dir: Path, *, now_sec: int | None = None) -> dict[str, Any]:
    now_ts = int(time.time()) if now_sec is None else now_sec
    etf_data: dict[str, Any] = {"BTC": [], "ETH": [], "summary": {}}
    conn_mac = _connect_ro(data_dir, "macro_market.db")
    try:
        for asset in ("BTC", "ETH"):
            rows, _ = _safe_query(
                conn_mac,
                "SELECT trade_date, total_musd, detail FROM etf_flows WHERE asset = ? ORDER BY trade_date DESC LIMIT 15",
                (asset,),
            )
            items = [
                {
                    "date": str(r["trade_date"]),
                    "total_musd": _num(r["total_musd"], 1),
                    "issuers": _parse_etf_detail(r["detail"]),
                }
                for r in reversed(rows)
            ]
            etf_data[asset] = items

            def _sum_window(slice_items: list[dict[str, Any]]) -> float | None:
                nums = [x["total_musd"] for x in slice_items if x["total_musd"] is not None]
                return _num(sum(nums), 1) if nums else None

            valid_items = [x for x in items if x["total_musd"] is not None]
            worst = min(valid_items, key=lambda x: x["total_musd"]) if valid_items else None
            best = max(valid_items, key=lambda x: x["total_musd"]) if valid_items else None

            etf_data["summary"][asset] = {
                "days": len(items),
                "sum_5d_musd": _sum_window(items[-5:]),
                "sum_10d_musd": _sum_window(items[-10:]),
                "sum_all_musd": _sum_window(items),
                "latest_date": items[-1]["date"] if items else None,
                "latest_musd": items[-1]["total_musd"] if items else None,
                "streak": _compute_etf_streak(items),
                "worst_day": {"date": worst["date"], "musd": worst["total_musd"]} if worst else None,
                "best_day": {"date": best["date"], "musd": best["total_musd"]} if best else None,
            }
    finally:
        if conn_mac:
            conn_mac.close()

    mstr_obj: dict[str, Any] = {"latest": None, "sec_latest": None, "series_7d": []}
    conn_mstr = _connect_ro(data_dir, "mstr.db")
    try:
        lat, _ = _safe_query(conn_mstr, "SELECT ts, holdings, mnav, btc_price FROM kpi_snapshots ORDER BY ts DESC LIMIT 1")
        if lat:
            mstr_obj["latest"] = {
                "ts": _int_or_zero(lat[0].get("ts")),
                "holdings": _num(lat[0].get("holdings"), 0),
                "mnav": _num(lat[0].get("mnav"), 3),
                "btc_price": _num(lat[0].get("btc_price"), 2),
            }
        sec, _ = _safe_query(
            conn_mstr,
            "SELECT filing_date, period, bought, sold, buy_usd_m, avg_buy, holdings, cost_b, avg_cost FROM btc_updates ORDER BY filing_date DESC LIMIT 1",
        )
        if sec:
            r = sec[0]
            mstr_obj["sec_latest"] = {
                "filing_date": str(r.get("filing_date") or ""),
                "period": str(r.get("period") or ""),
                "bought": _num(r.get("bought"), 0),
                "buy_usd_m": _num(r.get("buy_usd_m"), 2),
                "avg_buy": _num(r.get("avg_buy"), 2),
                "holdings": _num(r.get("holdings"), 0),
                "cost_b": _num(r.get("cost_b"), 3),
                "avg_cost": _num(r.get("avg_cost"), 2),
            }
        hist, _ = _safe_query(
            conn_mstr,
            "SELECT ts, mnav, btc_price FROM kpi_snapshots WHERE ts >= ? ORDER BY ts ASC",
            (now_ts - 7 * 86400,),
        )
        buckets: dict[int, dict[str, Any]] = {}
        for r in hist:
            ts_v = _int_or_zero(r.get("ts"))
            mv = _num(r.get("mnav"), 3)
            if ts_v <= 0 or mv is None:
                continue
            b = ts_v // 7200 * 7200
            buckets[b] = {"ts": ts_v, "mnav": mv, "btc_price": _num(r.get("btc_price"), 0)}
        mstr_obj["series_7d"] = [buckets[k] for k in sorted(buckets.keys())][-84:]
    finally:
        if conn_mstr:
            conn_mstr.close()

    hl_obj: dict[str, Any] = {"platform": None, "assistance_fund": None, "top_markets": []}
    conn_hl = _connect_ro(data_dir, "hyperliquid.db")
    try:
        plat, _ = _safe_query(conn_hl, "SELECT ts, total_oi_usd, total_vol_usd FROM platform_ticks ORDER BY ts DESC LIMIT 1")
        if plat:
            hl_obj["platform"] = {
                "ts": _int_or_zero(plat[0].get("ts")),
                "total_oi_usd": _num(plat[0].get("total_oi_usd"), 0),
                "total_vol_usd": _num(plat[0].get("total_vol_usd"), 0),
            }
        af, _ = _safe_query(conn_hl, "SELECT ts, hype, usdc FROM af_snapshots ORDER BY ts DESC LIMIT 1")
        af_24h, _ = _safe_query(
            conn_hl,
            "SELECT hype FROM af_snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
            (now_ts - 86400,),
        )
        if af:
            h_now = _num(af[0].get("hype"), 4)
            h_prev = _num(af_24h[0].get("hype"), 4) if af_24h else None
            hl_obj["assistance_fund"] = {
                "ts": _int_or_zero(af[0].get("ts")),
                "hype": _num(h_now, 2),
                "usdc": _num(af[0].get("usdc"), 2),
                "hype_chg_24h": _num(h_now - h_prev, 2) if (h_now is not None and h_prev is not None) else None,
            }
        coins, _ = _safe_query(
            conn_hl,
            """
            SELECT coin, mark, oi_coins, funding, day_vol
            FROM coin_ticks
            WHERE ts = (SELECT MAX(ts) FROM coin_ticks)
            ORDER BY day_vol DESC
            LIMIT 12
            """,
        )
        for c in coins:
            mark = _num(c.get("mark"), 6)
            oi_coins = _num(c.get("oi_coins"), 6)
            f1h = _num(c.get("funding"), 8)
            hl_obj["top_markets"].append({
                "coin": str(c.get("coin") or ""),
                "mark": _num(mark, 4),
                "oi_usd": _num(mark * oi_coins, 0) if (mark is not None and oi_coins is not None) else None,
                "day_vol": _num(c.get("day_vol"), 0),
                "funding_1h_pct": _num(f1h * 100.0, 4) if f1h is not None else None,
                "funding_ann_pct": _num(f1h * 24 * 365 * 100.0, 2) if f1h is not None else None,
            })
    finally:
        if conn_hl:
            conn_hl.close()

    # Binance OI + Extreme Funding Dual Radar (anomalies FIRST, then liquid major anchors)
    oi_radar = []
    conn_opp = _connect_ro(data_dir, "opportunity.db")
    try:
        rows, _ = _safe_query(
            conn_opp,
            """
            SELECT symbol, base, price, oi_usd, vol_24h, chg_1h_oi, chg_1h_px, funding_rate, updated_at
            FROM bn_oi_latest
            WHERE price IS NOT NULL AND oi_usd IS NOT NULL
            """,
        )
        ranked: list[tuple[tuple[int, float], dict[str, Any]]] = []
        seen_base_norm: set[str] = set()
        for r in rows:
            sym = str(r.get("symbol") or "")
            base = str(r.get("base") or "")
            oi_usd = _num(r.get("oi_usd"), 0) or 0.0
            vol_24h = _num(r.get("vol_24h"), 0) or 0.0
            oi1h = _num(r.get("chg_1h_oi"), 2)
            px1h = _num(r.get("chg_1h_px"), 2)
            fr = _num(r.get("funding_rate"), 8)
            fr8h = _num(fr * 100.0, 4) if fr is not None else None
            fr_ann = _num(fr * 3.0 * 365.0 * 100.0, 1) if fr is not None else None

            is_extreme_neg = fr8h is not None and fr8h <= -0.10 and vol_24h >= 20_000_000
            is_extreme_pos = fr8h is not None and fr8h >= 0.08 and vol_24h >= 20_000_000
            is_oi_surge = oi1h is not None and oi1h >= 2.0

            if is_extreme_neg:
                regime, tone, prio = "极端负费率·防轧空", "danger", 1
                sort_val = -(abs(fr8h or 0.0))
            elif is_extreme_pos:
                regime, tone, prio = "极端正费率·防回调", "warn", 2
                sort_val = -(abs(fr8h or 0.0))
            elif is_oi_surge and (px1h is not None and px1h >= 0.5):
                regime, tone, prio = "追多拥挤·忌追高", "warn", 3
                sort_val = -(oi1h or 0.0)
            elif is_oi_surge and (px1h is not None and px1h <= -0.5):
                regime, tone, prio = "空头扎堆·防逼空", "squeeze", 3
                sort_val = -(oi1h or 0.0)
            elif is_oi_surge:
                regime, tone, prio = "杠杆建仓·观望", "info", 4
                sort_val = -(oi1h or 0.0)
            elif base in MAJOR_OI_ANCHORS and sym.endswith("USDT"):
                regime, tone, prio = "主流锚点", "muted", 5
                sort_val = -oi_usd
            else:
                continue

            item = {
                "symbol": sym,
                "base": base,
                "price": _num(r.get("price"), 6),
                "oi_usd": oi_usd,
                "vol_24h": vol_24h,
                "chg_1h_oi_pct": oi1h,
                "chg_1h_px_pct": px1h,
                "funding_8h_pct": fr8h,
                "funding_ann_pct": fr_ann,
                "regime": regime,
                "tone": tone,
                "updated_at": _int_or_zero(r.get("updated_at")),
            }
            ranked.append(((prio, sort_val), item))

        ranked.sort(key=lambda x: x[0])
        for _, item in ranked:
            # Prefer USDT perpetual when duplicate base appears in muted anchors
            key_b = item["base"]
            if item["tone"] == "muted" and key_b in seen_base_norm:
                continue
            seen_base_norm.add(key_b)
            oi_radar.append(item)
            if len(oi_radar) >= 16:
                break
    finally:
        if conn_opp:
            conn_opp.close()

    return {
        "etf_flows": etf_data,
        "mstr": mstr_obj,
        "hyperliquid": hl_obj,
        "bn_oi_radar": oi_radar,
    }


def _parse_promo_expiry(text: str, now_dt: datetime) -> datetime | None:
    """Extract the explicit end date from promo text to filter out expired campaigns."""
    # Pattern 1: explicit range end date "~ 12-18 08:00" / "至 12-01 07:59" (iterate all matches so
    # earlier numeric ranges like "估值1.26~1.41万亿" do not mask a real date range later in the text)
    for m_range in re.finditer(
        r"[~～至到]\s*(?:(\d{4})[-/.年])?(\d{1,2})([-/.月])(\d{1,2})日?(?![\d.%万亿U美元])(?:\s*(\d{1,2}):(\d{2}))?",
        text,
    ):
        yr_s, mo_s, sep, dy_s, hh_s, mm_s = m_range.groups()
        mo, dy = int(mo_s), int(dy_s)
        if sep == "." and not yr_s and not hh_s:
            # Skip ambiguous decimal ranges like "1.26~1.41" unless accompanied by year or time
            continue
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            yr = int(yr_s) if yr_s else (now_dt.year + 1 if mo < now_dt.month - 6 else now_dt.year)
            hh = int(hh_s) if hh_s else 23
            mm = int(mm_s) if mm_s else 59
            try:
                return datetime(yr, mo, dy, min(hh, 23), min(mm, 59), tzinfo=BJT)
            except ValueError:
                pass
    # Pattern 2: "最迟11月20日" / "截止 11月24日"
    for m_end in re.finditer(r"(?:最迟|截止|截至)\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日?", text):
        yr_s, mo_s, dy_s = m_end.groups()
        mo, dy = int(mo_s), int(dy_s)
        if 1 <= mo <= 12 and 1 <= dy <= 31:
            yr = int(yr_s) if yr_s else (now_dt.year + 1 if mo < now_dt.month - 6 else now_dt.year)
            try:
                return datetime(yr, mo, dy, 23, 59, tzinfo=BJT)
            except ValueError:
                pass
    return None


def _parse_clean_promo(r: dict[str, Any], now_dt: datetime) -> dict[str, Any] | None:
    title = str(r.get("title") or "").strip()
    raw_summary = str(r.get("summary_text") or "").strip()
    if not title or GENERIC_PRIVATE_RE.search(title) or GENERIC_PRIVATE_RE.search(raw_summary):
        return None

    combined = f"{title}\n{raw_summary}"
    # Reject obvious past-year relics (e.g. 2024/2025 announcements still sitting in DB)
    years_found = [int(y) for y in re.findall(r"\b(202\d)\b", combined)]
    if years_found and max(years_found) < now_dt.year:
        return None

    # Reject empty boilerplate fallback announcements seeded without real reward/window details
    if "详见正文规则" in raw_summary and "详见官方详情页" in raw_summary:
        return None

    # Reject promos whose explicit deadline has already passed
    expiry_dt = _parse_promo_expiry(combined, now_dt)
    if expiry_dt is not None and expiry_dt < now_dt:
        return None

    # Reject stale promos (>14 days old, or titled with an explicit date >10 days ago without a future deadline)
    created_at = _int_or_zero(r.get("created_at"))
    if expiry_dt is None:
        if created_at > 0 and (int(now_dt.timestamp()) - created_at) > 14 * 86400:
            return None
        m_title_dt = re.search(r"\((\d{4})-(\d{2})-(\d{2})\)", title)
        if m_title_dt:
            try:
                td = datetime(int(m_title_dt.group(1)), int(m_title_dt.group(2)), int(m_title_dt.group(3)), tzinfo=BJT)
                if (now_dt - td).days > 10:
                    return None
            except ValueError:
                pass

    reward = ""
    window = ""
    note_parts: list[str] = []
    headline_zh = ""
    for ln in raw_summary.splitlines():
        s = ln.strip()
        if not s or _DIVIDER_RE.search(s):
            continue
        if s.startswith(("🔥", "🎁", "🔗")):
            continue
        if re.match(r"^(?:📌|🏷️)\s*(?:标题|类型)[：:]", s):
            continue
        clean_ln = _URL_IN_TEXT_RE.sub("", _strip_emojis(s)).strip()
        if not clean_ln:
            continue
        if clean_ln.startswith(("收益/奖池：", "奖池/收益：", "收益/奖池:", "奖池/收益:")):
            val = re.sub(r"^(?:收益/奖池|奖池/收益)[：:]\s*", "", clean_ln)
            if val and "详见正文" not in val:
                reward = val
        elif clean_ln.startswith(("活动时间：", "活动时间:")):
            val = re.sub(r"^活动时间[：:]\s*", "", clean_ln)
            if val and "详见官方" not in val:
                window = val
        elif clean_ln.startswith(("额度/门槛：", "额度/门槛:")):
            note_parts.append(clean_ln)
        elif clean_ln.startswith(("套保与防坑：", "套保与防坑:", "大白话结论：", "大白话结论:", "参与路径：", "参与路径:")):
            val = re.sub(r"^(?:套保与防坑|大白话结论|参与路径)[：:]\s*", "", clean_ln)
            if val and "详见公告原文" not in val and "规则相对简单、资金磨损较小" not in val:
                note_parts.append(val)
        elif s.startswith("📌"):
            headline_zh = clean_ln
        elif not clean_ln.startswith(("怎么参与", "原文")):
            note_parts.append(clean_ln)

    worth_raw = str(r.get("worth_level") or "")
    ptype_raw = str(r.get("promo_type") or "")
    if any(k in (worth_raw + ptype_raw) for k in ("反撸", "刷量", "竞赛", "⚠️")):
        tier, tier_label = "WARN", "刷量竞赛 · 慎防反撸"
    elif "重点" in worth_raw:
        tier, tier_label = "HIGH", "低风险重点关注"
    else:
        tier, tier_label = "LOW_RISK", "常规公开活动"

    display_title = headline_zh if (headline_zh and len(headline_zh) >= 8) else _clean_cut(title, 96)
    summary_clean = _clean_cut(" · ".join(note_parts) if note_parts else _strip_emojis(raw_summary), 160)
    if summary_clean.startswith("http"):
        summary_clean = ""

    return {
        "exchange": _normalize_exchange(str(r.get("exchange") or "")),
        "tier": tier,
        "tier_label": tier_label,
        "worth_level": tier_label,
        "promo_type": _strip_emojis(ptype_raw)[:32],
        "title": _clean_cut(display_title, 110),
        "reward": _clean_cut(reward, 110),
        "window": _clean_cut(window, 64),
        "summary": summary_clean,
        "url": _clean_promo_url(r.get("url")),
        "created_at": created_at,
    }


def export_labs(data_dir: Path, *, now_dt: datetime | None = None) -> dict[str, Any]:
    cur_dt = datetime.now(BJT) if now_dt is None else now_dt
    dca_runs = []
    latest_summary = None
    conn_dca = _connect_ro(data_dir, "dca_sim.db")
    try:
        rows_desc, _ = _safe_query(
            conn_dca,
            "SELECT date_bjt, summary_json FROM runs ORDER BY date_bjt DESC LIMIT 90",
        )
        for r in reversed(rows_desc):
            try:
                sj = json.loads(r["summary_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(sj, dict):
                continue
            point: dict[str, Any] = {"date": str(r.get("date_bjt") or "")}
            has_strat = False
            for out_key, src_keys in (
                ("FIXED", ("FIXED",)),
                ("VA", ("VA",)),
                ("MAFG", ("MAFG", "MA200_FNG")),
            ):
                s = next((sj.get(k) for k in src_keys if isinstance(sj.get(k), dict)), None)
                if isinstance(s, dict):
                    has_strat = True
                    point[out_key] = {
                        "in": _num(s.get("in"), 2),
                        "mv": _num(s.get("mv"), 2),
                        "pnl": _num(s.get("pnl"), 2),
                        "ret_pct": _num(s.get("ret"), 2),
                        "days": max(0, _int_or_zero(s.get("days"))),
                    }
            if has_strat:
                dca_runs.append(point)
                latest_summary = sj
    finally:
        if conn_dca:
            conn_dca.close()

    latest_strategies = {}
    if isinstance(latest_summary, dict):
        strat_spec = (
            ("FIXED", ("FIXED",), "固定定投（每日每标的 100U）"),
            ("VA", ("VA",), "价值平均法（跌多买、涨少买）"),
            ("MAFG", ("MAFG", "MA200_FNG"), "200日均线 + 恐慌指数加权"),
        )
        for out_key, src_keys, label in strat_spec:
            s = next((latest_summary.get(k) for k in src_keys if isinstance(latest_summary.get(k), dict)), None)
            if not isinstance(s, dict):
                continue
            per = {}
            if isinstance(s.get("per"), dict):
                for asset, ret in s["per"].items():
                    ak = str(asset).strip().upper()
                    if ASSET_KEY_RE.match(ak):
                        per[ak] = _num(ret, 2)
            latest_strategies[out_key] = {
                "label": label,
                "in": _num(s.get("in"), 2),
                "mv": _num(s.get("mv"), 2),
                "pnl": _num(s.get("pnl"), 2),
                "ret_pct": _num(s.get("ret"), 2),
                "days": max(0, _int_or_zero(s.get("days"))),
                "per_asset_ret_pct": per,
            }

    promos = []
    seen_urls: set[str] = set()
    conn_pr = _connect_ro(data_dir, "exchange_promos.db")
    try:
        rows, _ = _safe_query(
            conn_pr,
            """
            SELECT exchange, promo_type, worth_level, title, url, summary_text, created_at
            FROM promos
            WHERE worth_level IS NOT NULL
              AND (worth_level LIKE '%重点%' OR worth_level LIKE '%低门槛%' OR worth_level LIKE '%反撸%' OR worth_level = '🟢')
            ORDER BY created_at DESC
            LIMIT 40
            """,
        )
        for r in rows:
            parsed = _parse_clean_promo(r, cur_dt)
            if not parsed:
                continue
            u = parsed["url"]
            if u and u in seen_urls:
                continue
            if u:
                seen_urls.add(u)
            promos.append(parsed)
            if len(promos) >= 12:
                break
    finally:
        if conn_pr:
            conn_pr.close()

    return {
        "dca_simulation": {
            "disclaimer": "纯虚拟模拟盘（无实盘资金、0.1% 模拟手续费），仅作公开量化方法论长期透明对照。",
            "latest_date": dca_runs[-1]["date"] if dca_runs else None,
            "strategies": latest_strategies,
            "history": dca_runs,
        },
        "exchange_promos": promos,
    }


def _clean_feed_source(raw_header: str, category: str) -> str:
    s = _strip_emojis(raw_header)
    s = _BRACKET_TAG_RE.sub("", s)
    s = _SCORE_RE.sub("", s)
    s = _REL_TIME_RE.sub("", s).strip(" ·•|-:/")
    # Strip internal node/wire prefixes repeatedly until clean source name remains
    for _ in range(3):
        nxt = _NODE_PREFIX_RE.sub("", s).strip(" ·•|-:/")
        if nxt == s:
            break
        s = nxt
    s = re.sub(r"\(\d{2}:\d{2}\)\s*$", "", s).strip()
    if s.startswith("@"):
        s = s[1:]
    if not s or len(s) > 28:
        return "交易所公告" if category == "交易所公告" else "Global Wire"
    return _normalize_exchange(s) if s.lower() in _EXCH_NORM else s


def _text_bigrams(s: str) -> set[str]:
    clean = re.sub(r"[\W_]+", "", (s or "").lower())
    if len(clean) < 2:
        return {clean} if clean else set()
    return {clean[i : i + 2] for i in range(len(clean) - 1)}


def _is_similar_headline(a: str, b: str) -> bool:
    ba, bb = _text_bigrams(a), _text_bigrams(b)
    if not ba or not bb:
        return False
    inter = len(ba & bb)
    union = len(ba | bb)
    if union > 0 and (inter / union) >= 0.44:
        return True
    # Catch repeated Kraken CAD / Chainflip / Cyber incident updates
    low_a, low_b = a.lower(), b.lower()
    for ent, kw in (("kraken", "加元"), ("chainflip", "被盗"), ("cyber", "跨链桥")):
        if ent in low_a and kw in low_a and ent in low_b and kw in low_b:
            return True
    return False


def _parse_feed_entry(r: dict[str, Any]) -> dict[str, Any] | None:
    msg = str(r.get("msg") or "").strip()
    if not msg or GENERIC_PRIVATE_RE.search(msg):
        return None
    if "X快讯·@" in msg or "推圈" in msg:
        return None

    cat = str(r.get("category") or "")
    lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
    if not lines:
        return None

    header_line = lines[0]
    source = _clean_feed_source(header_line, cat)

    headline = ""
    impact_label = ""
    impact_tone = "neutral"
    market_ref = ""
    extra_lines: list[str] = []

    for ln in lines[1:]:
        if ln.startswith("📌"):
            headline = _strip_emojis(ln).strip()
        elif ln.startswith(("🔴", "🟢", "🟡", "⚠️")):
            if ln.startswith("🔴"):
                impact_tone = "bearish"
            elif ln.startswith("🟢"):
                impact_tone = "bullish"
            elif ln.startswith("⚠️"):
                impact_tone = "warn"
            else:
                impact_tone = "neutral"
            impact_label = _strip_emojis(ln).strip()
        elif ln.startswith(("🎯", "🔗")):
            market_ref = _strip_emojis(ln).strip()
        elif ln.startswith(("🐦", "🇰🇷")):
            continue  # Skip unverified noisy social quotes from public wire
        else:
            cl = _strip_emojis(ln).strip()
            if cl and not cl.startswith("声明："):
                extra_lines.append(cl)

    if not headline:
        headline = extra_lines.pop(0) if extra_lines else _strip_emojis(header_line)
    if not headline:
        return None

    # Drop secondhand ETF flow flashes that can conflict with the canonical Farside ETF matrix
    if cat == "秒级快讯" and re.search(r"美国(?:比特币|以太坊)现货ETF净(?:流出|流入)", headline):
        return None

    # Sanity-check mismatched impact tags & bogus XMR coordinates from legacy archive entries
    if re.search(r"禁空|卖空禁令|股票出借|融资保证金|救市", headline) and "被盗" in impact_label:
        impact_label, impact_tone = "政策/市场干预（关注流动性与波动）", "neutral"
    if re.search(r"证监会调查|会计问题|SEC.*调查", headline) and "关税" in impact_label:
        impact_label, impact_tone = "利空 · 监管/基本面核查风险", "bearish"
    if market_ref and "XMR" in market_ref and not re.search(r"XMR|Monero|门罗|Tornado", headline, re.IGNORECASE):
        market_ref = ""

    # Clean up redundant "(先手坐标：...)" prefix
    market_ref = re.sub(r"^(?:先手坐标|联动盯盘)[：:]\s*", "", market_ref).strip()

    return {
        "id": _int_or_zero(r.get("id")),
        "ts": _int_or_zero(r.get("ts")),
        "dt_bjt": str(r.get("dt_bjt") or ""),
        "level": str(r.get("level") or "normal"),
        "category": cat,
        "source": source,
        "headline": _clean_cut(headline, 180),
        "impact_label": _clean_cut(impact_label, 72),
        "impact_tone": impact_tone,
        "market_ref": _clean_cut(market_ref, 110),
        "body": _clean_cut(" ".join(extra_lines), 220) if extra_lines else "",
    }


def export_feed(data_dir: Path, *, now_sec: int | None = None) -> dict[str, Any]:
    now_ts = int(time.time()) if now_sec is None else now_sec
    items: list[dict[str, Any]] = []
    short_analysis = None
    conn_arc = _connect_ro(data_dir, "archive_14d.db")
    try:
        # Fetch latest daily objective analysis separately
        da_rows, _ = _safe_query(
            conn_arc,
            "SELECT id, ts, dt_bjt, level, category, msg FROM message_archive WHERE category = '每日短线研判' ORDER BY id DESC LIMIT 1",
        )
        if da_rows:
            r = da_rows[0]
            msg = str(r.get("msg") or "").strip()
            if msg and not GENERIC_PRIVATE_RE.search(msg):
                lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
                body_lines = [
                    _strip_emojis(ln)
                    for ln in lines[1:]
                    if not ln.startswith(("⚠️", "声明：")) and "不构成任何投资建议" not in ln
                ]
                body = " ".join(body_lines).strip()
                # Clean known legacy attribution typo in archived text
                body = re.sub(r"伊朗法尔斯通讯社援引自家记者的关税消息", "中东地缘冲突相关消息", body)
                ts_v = _int_or_zero(r.get("ts"))
                age_h = round(max(0.0, (now_ts - ts_v) / 3600.0), 1) if ts_v > 0 else None
                short_analysis = {
                    "id": _int_or_zero(r.get("id")),
                    "ts": ts_v,
                    "dt_bjt": str(r.get("dt_bjt") or ""),
                    "age_hours": age_h,
                    "is_stale": bool(age_h is not None and age_h >= 14.0),
                    "level": str(r.get("level") or "normal"),
                    "category": "每日短线研判",
                    "headline": "每日 17:00 BJT 客观短线研判",
                    "body": _clean_cut(body, 360),
                }

        # Reserve guaranteed per-category quotas so Exchange Announcements are NEVER starved by fast wire
        quotas = (("秒级快讯", 55, 24), ("交易所公告", 20, 8), ("情报预警", 18, 6))
        for cat, sql_lim, keep_lim in quotas:
            rows, _ = _safe_query(
                conn_arc,
                "SELECT id, ts, dt_bjt, level, category, msg FROM message_archive WHERE category = ? ORDER BY id DESC LIMIT ?",
                (cat, sql_lim),
            )
            cat_kept = 0
            for r in rows:
                parsed = _parse_feed_entry(r)
                if not parsed:
                    continue
                dup = False
                for existing in items:
                    if abs(existing["ts"] - parsed["ts"]) <= 8 * 3600 and _is_similar_headline(existing["headline"], parsed["headline"]):
                        dup = True
                        break
                if dup:
                    continue
                items.append(parsed)
                cat_kept += 1
                if cat_kept >= keep_lim:
                    break

        items.sort(key=lambda x: (x["ts"], x["id"]), reverse=True)
    finally:
        if conn_arc:
            conn_arc.close()

    return {
        "daily_analysis": short_analysis,
        "items": items,
    }


def _synthesize_regime_and_signals(
    overview: dict[str, Any],
    arb: dict[str, Any],
    flows: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bd = overview.get("bottom_dashboard") or {}
    fng = overview.get("fear_greed") or {}
    macro = overview.get("macro_quotes") or []
    vix = next((m for m in macro if m.get("symbol") == ".VIX"), None)
    us10y = next((m for m in macro if m.get("symbol") == "US10Y"), None)

    green = _int_or_zero(bd.get("green"))
    total = _int_or_zero(bd.get("total")) or 7
    fng_val = fng.get("value")
    vix_px = vix.get("price") if vix else None

    if green >= 5:
        reg_label, reg_tone = "周期共振观察区", "bullish"
        reg_hint = "底部结构条件多数转绿，可结合分批纪律观察右侧承接"
    elif (fng_val is not None and fng_val <= 20) or (vix_px is not None and vix_px >= 28):
        reg_label, reg_tone = "高波防御 · 极度恐慌", "danger"
        reg_hint = "美股波动与加密情绪双承压，优先控制回撤、避开高溢价与追多拥挤"
    elif fng_val is not None and fng_val >= 72:
        reg_label, reg_tone = "情绪偏热 · 留意拥挤", "warn"
        reg_hint = "市场贪婪度抬升，警惕高费率与追高回撤"
    else:
        reg_label, reg_tone = "震荡拉锯 · 结构选边", "neutral"
        reg_hint = "底部条件尚未共振，聚焦低风险贴水、跨市场价差与资金流拐点"

    regime = {
        "label": reg_label,
        "tone": reg_tone,
        "hint": reg_hint,
        "bottom_score": f"{green}/{total}",
        "fng": fng_val,
        "vix": vix_px,
        "us10y": us10y.get("price") if us10y else None,
    }

    signals: list[dict[str, Any]] = []

    # 1. Extreme QDII premium risk
    qdii = overview.get("qdii_premium") or []
    high_qdii = [q for q in qdii if q.get("prem_pct") is not None and q["prem_pct"] >= 15.0]
    if high_qdii:
        top_q = high_qdii[0]
        signals.append({
            "severity": "danger",
            "tag": "高溢价避坑",
            "title": f"{top_q['name']} ({top_q['code']})",
            "metric": f"+{top_q['prem_pct']:.2f}%",
            "detail": f"共 {len(high_qdii)} 只场内美股 QDII 溢价 ≥15%，谨防溢价回落双杀",
            "target_tab": "overview",
        })

    # 2. Extreme Binance funding or OI surge
    oi_rows = flows.get("bn_oi_radar") or []
    anomalies = [r for r in oi_rows if r.get("tone") in ("danger", "warn", "squeeze")]
    if anomalies:
        top_a = anomalies[0]
        if top_a["tone"] == "danger" and top_a.get("funding_8h_pct") is not None:
            metric = f"8h {top_a['funding_8h_pct']:+.2f}%"
            detail = f"折合年化 {top_a.get('funding_ann_pct', 0):+.0f}% · 留意轧空与现货停充提风险"
        else:
            metric = f"1h OI {top_a.get('chg_1h_oi_pct', 0):+.2f}%"
            detail = f"1h 涨跌 {top_a.get('chg_1h_px_pct', 0):+.2f}% · {top_a.get('regime', '')}"
        signals.append({
            "severity": "danger" if top_a["tone"] == "danger" else "warn",
            "tag": "合约资金异动",
            "title": f"{top_a['base']} 永续 ({top_a['regime']})",
            "metric": metric,
            "detail": detail,
            "target_tab": "flows",
        })

    # 3. MSTR mNAV break below 1.0 or high premium
    mstr_lat = (flows.get("mstr") or {}).get("latest") or {}
    mnav = mstr_lat.get("mnav")
    if mnav is not None and (mnav < 1.0 or mnav >= 2.2):
        signals.append({
            "severity": "warn" if mnav < 1.0 else "info",
            "tag": "机构估值锚",
            "title": "Strategy (MSTR) mNAV 折价" if mnav < 1.0 else "Strategy (MSTR) 高溢价",
            "metric": f"{mnav:.3f}x",
            "detail": f"持仓 {int(mstr_lat.get('holdings') or 0):,} BTC · 跌破 1.0 反映币股情绪承压",
            "target_tab": "flows",
        })

    # 4. Deep far-month CSI futures discount cushion
    csi = arb.get("csi_basis") or []
    far_csi = [c for c in csi if str(c.get("contract") or "").endswith("2706") and c.get("ann_pct") is not None]
    if far_csi:
        far_csi.sort(key=lambda c: c["ann_pct"])
        best_c = far_csi[0]
        signals.append({
            "severity": "opportunity",
            "tag": "远月贴水垫",
            "title": f"{best_c['contract']} 远月深度贴水",
            "metric": f"年化 {best_c['ann_pct']:+.2f}%",
            "detail": f"贴水 {best_c['basis_pts']:+.1f} 点 · 剩余 {best_c['days_left']} 天，适合多头替代参考",
            "target_tab": "arb",
        })

    # 5. Cross-market spread Z-score deviation OR major wallet halt
    spreads = arb.get("spreads") or {}
    best_sp = None
    for sp in spreads.values():
        z = (sp.get("band_72h") or {}).get("zscore")
        if z is not None and (best_sp is None or abs(z) > abs(best_sp[0])):
            best_sp = (z, sp)
    if best_sp and abs(best_sp[0]) >= 1.1:
        z, sp = best_sp
        lat = sp.get("latest") or {}
        signals.append({
            "severity": "info",
            "tag": "跨市场偏离",
            "title": sp.get("title", "跨市场价差"),
            "metric": f"{z:+.2f}σ",
            "detail": f"当前价差 {lat.get('spread_pct', 0):+.2f}%（72h 均值 {(sp.get('band_72h') or {}).get('mean', 0):.2f}%）",
            "target_tab": "arb",
        })
    else:
        halts = (arb.get("wallet_status") or {}).get("major_halts") or []
        if halts:
            coins_str = " / ".join(sorted({h["coin"] for h in halts})[:4])
            signals.append({
                "severity": "warn",
                "tag": "充提防飞刀",
                "title": f"{len(halts)} 项主流币充提受限",
                "metric": coins_str,
                "detail": "跨所搬砖或对冲前务必核对链上充提状态",
                "target_tab": "arb",
            })

    return regime, signals[:5]


def build_bundle(data_dir: Path) -> dict[str, Any]:
    now_dt = datetime.now(BJT)
    now_sec = int(now_dt.timestamp())
    meta = {
        "project": "DanClub Open Terminal",
        "license": "MIT",
        "generated_at_bjt": now_dt.strftime("%Y-%m-%d %H:%M:%S +08:00"),
        "generated_ts": now_sec,
        "disclaimer": "纯公开市场数据聚合与开源量化研究工具，不构成任何投资建议，零私有数据。",
        "endpoints": {
            "index": "./api/v1/index.json",
            "overview": "./api/v1/overview.json",
            "arb": "./api/v1/arb.json",
            "flows": "./api/v1/flows.json",
            "labs": "./api/v1/labs.json",
            "feed": "./api/v1/feed.json",
        },
    }
    overview = export_overview(data_dir, now_sec=now_sec)
    arb = export_arb(data_dir, now_sec=now_sec)
    flows = export_flows(data_dir, now_sec=now_sec)
    labs = export_labs(data_dir, now_dt=now_dt)
    feed = export_feed(data_dir, now_sec=now_sec)

    regime, desk_signals = _synthesize_regime_and_signals(overview, arb, flows)
    overview["market_regime"] = regime
    overview["desk_signals"] = desk_signals

    return {
        "meta": meta,
        "overview": overview,
        "arb": arb,
        "flows": flows,
        "labs": labs,
        "feed": feed,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(path))
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only public market data exporter")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Path to SQLite data directory")
    parser.add_argument("--stdout", action="store_true", help="Write full bundle JSON to stdout")
    parser.add_argument("--out-dir", type=Path, help="Write split JSON files (enforces privacy firewall)")
    parser.add_argument("--blocklist", type=Path, default=None, help="Optional private blocklist file")
    args = parser.parse_args()

    bundle = build_bundle(args.data_dir)
    if args.out_dir:
        if _pg is None:
            raise RuntimeError("standalone --out-dir 缺少 privacy_guard 模块，拒绝绕过隐私校验写入")
        _pg.assert_clean_payload(bundle, blocklist_path=args.blocklist)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for key, payload in (
            ("index", bundle["meta"]),
            ("overview", {"meta": bundle["meta"], **bundle["overview"]}),
            ("arb", {"meta": bundle["meta"], **bundle["arb"]}),
            ("flows", {"meta": bundle["meta"], **bundle["flows"]}),
            ("labs", {"meta": bundle["meta"], **bundle["labs"]}),
            ("feed", {"meta": bundle["meta"], **bundle["feed"]}),
        ):
            _atomic_write_json(args.out_dir / f"{key}.json", payload)
    if args.stdout or not args.out_dir:
        sys.stdout.write(json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
