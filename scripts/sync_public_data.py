#!/usr/bin/env python3
"""M6 One-Way Public Data Sync & GitHub Pages Publisher for DanClub Open Terminal.

Architecture (Strict One-Way Zero-Trust):
  iowa (read-only exporter stdout, bounded byte read)
  -> M6 (strict schema + bounded int checks + fail-closed privacy_guard firewall)
  -> verified in-memory bytes written to BOTH out_dir and a clean symlink-free staging dir
  -> SHA-256 digest & exact git ls-files verification -> single orphan commit push to gh-pages.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from privacy_guard import (  # noqa: E402
    ALLOWED_PROMO_DOMAINS,
    PrivacyViolationError,
    assert_clean_payload,
    sanitize_public_url,
    sanitize_text,
)

SITE_DIR = REPO_ROOT / "site"
API_DIR = SITE_DIR / "api" / "v1"
LOCK_FILE = Path("/tmp/danclub_open_sync.lock")
DEFAULT_SSH_HOST = os.environ.get("DANCLUB_SSH_HOST", "")
DEFAULT_REMOTE_EXPORTER = os.environ.get("DANCLUB_REMOTE_EXPORTER", "./public_terminal_exporter.py")
REQUIRED_TOP_KEYS = ("meta", "overview", "arb", "flows", "labs", "feed")
MAX_REMOTE_BYTES = 5 * 1024 * 1024

# Explicit whitelist of frontend static files allowed in gh-pages publish
WHITELISTED_STATIC_FILES: tuple[str, ...] = (
    "index.html",
    "assets/style.css",
    "assets/app.js",
)


def _redact_repo_url(url: str) -> str:
    """Strip any userinfo/credentials from HTTPS repo URLs before logging."""
    try:
        p = urlparse(url)
        if p.scheme in ("http", "https") and (p.username or p.password):
            host = p.hostname or ""
            port = f":{p.port}" if p.port else ""
            return p._replace(netloc=f"***@{host}{port}").geturl()
    except ValueError:
        pass
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", url)


def _require_bounded_int(val: Any, field: str, lo: int = 0, hi: int = 10_000_000) -> int:
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(f"字段类型非法 ({field}): 期望 int，收到 {type(val).__name__}")
    if val < lo or val > hi:
        raise ValueError(f"字段数值越界 ({field}): {val} 不在 [{lo}, {hi}]")
    return val


def _clean_bundle(bundle: dict[str, Any], *, blocklist_path: Path | None = None) -> dict[str, Any]:
    """Deep-sanitize, strictly validate count/type invariants, and enforce fail-closed firewall."""
    if not isinstance(bundle, dict):
        raise ValueError("导出数据不是合法 JSON 对象")
    for k in REQUIRED_TOP_KEYS:
        if k not in bundle or not isinstance(bundle[k], dict):
            raise ValueError(f"导出数据缺少必要顶层段: {k}")

    # 1. Overview strict type validation + text cleanup
    bd = bundle["overview"].get("bottom_dashboard")
    if not isinstance(bd, dict):
        raise ValueError("overview.bottom_dashboard 缺失或类型错误")
    for k in ("green", "yellow", "red", "total"):
        _require_bounded_int(bd.get(k), f"overview.bottom_dashboard.{k}", 0, 20)
    for it in bd.get("items", []):
        if isinstance(it, dict):
            it["detail"] = sanitize_text(it.get("detail"), max_len=240)
    miner = bundle["overview"].get("btc_miner")
    if isinstance(miner, dict) and miner.get("remaining_blocks") is not None:
        _require_bounded_int(miner.get("remaining_blocks"), "overview.btc_miner.remaining_blocks", 0, 100000)

    # 2. Arb strict integer/type validation (closes stored XSS via malformed count strings)
    for i, c in enumerate(bundle["arb"].get("csi_basis", [])):
        if not isinstance(c, dict):
            raise ValueError(f"arb.csi_basis[{i}] 不是对象")
        _require_bounded_int(c.get("days_left"), f"arb.csi_basis[{i}].days_left", 0, 5000)
        _require_bounded_int(c.get("oi", 0), f"arb.csi_basis[{i}].oi", 0, 100_000_000)
        _require_bounded_int(c.get("vol", 0), f"arb.csi_basis[{i}].vol", 0, 100_000_000)

    ws = bundle["arb"].get("wallet_status")
    if not isinstance(ws, dict):
        raise ValueError("arb.wallet_status 缺失或类型错误")
    if not isinstance(ws.get("ok"), bool):
        raise ValueError("arb.wallet_status.ok 必须为 bool")
    _require_bounded_int(ws.get("exchanges_covered", 0), "arb.wallet_status.exchanges_covered", 0, 20)
    for i, w in enumerate(ws.get("summary", [])):
        if not isinstance(w, dict):
            raise ValueError(f"arb.wallet_status.summary[{i}] 不是对象")
        for fld in ("total_coins", "both_halted", "dep_only_halted", "wd_only_halted"):
            _require_bounded_int(w.get(fld), f"arb.wallet_status.summary[{i}].{fld}", 0, 100_000)

    # 3. Feed text sanitization
    feed = bundle["feed"]
    if isinstance(feed.get("daily_analysis"), dict):
        feed["daily_analysis"]["headline"] = sanitize_text(feed["daily_analysis"].get("headline"), max_len=140)
        feed["daily_analysis"]["body"] = sanitize_text(feed["daily_analysis"].get("body"), max_len=420)
    clean_items = []
    for it in feed.get("items", []):
        if not isinstance(it, dict):
            continue
        it["headline"] = sanitize_text(it.get("headline"), max_len=140)
        it["body"] = sanitize_text(it.get("body"), max_len=360)
        if it["headline"]:
            clean_items.append(it)
    feed["items"] = clean_items[:35]

    # 4. Promos URL allowlist + text sanitization
    labs = bundle["labs"]
    clean_promos = []
    for pr in labs.get("exchange_promos", []):
        if not isinstance(pr, dict):
            continue
        pr["title"] = sanitize_text(pr.get("title"), max_len=140)
        pr["summary"] = sanitize_text(pr.get("summary"), max_len=260)
        pr["url"] = sanitize_public_url(pr.get("url"), allowed_domains=ALLOWED_PROMO_DOMAINS)
        if pr["title"]:
            clean_promos.append(pr)
    labs["exchange_promos"] = clean_promos[:16]

    # 5. Fail-closed privacy firewall across the entire payload
    assert_clean_payload(bundle, blocklist_path=blocklist_path)
    return bundle


def build_verified_api_bytes(bundle: dict[str, Any], *, blocklist_path: Path | None = None) -> dict[str, bytes]:
    """Serialize all 6 split API payloads in memory with strict allow_nan=False and privacy check."""
    meta = bundle["meta"]
    outputs = {
        "index.json": meta,
        "overview.json": {"meta": meta, **bundle["overview"]},
        "arb.json": {"meta": meta, **bundle["arb"]},
        "flows.json": {"meta": meta, **bundle["flows"]},
        "labs.json": {"meta": meta, **bundle["labs"]},
        "feed.json": {"meta": meta, **bundle["feed"]},
    }
    encoded: dict[str, bytes] = {}
    for name, payload in outputs.items():
        assert_clean_payload(payload, blocklist_path=blocklist_path)
        encoded[name] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return encoded


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            fh.write(data)
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


def write_api_bytes(api_bytes: dict[str, bytes], out_dir: Path) -> list[Path]:
    written: list[Path] = []
    for name, raw in api_bytes.items():
        target = out_dir / name
        _atomic_write_bytes(target, raw)
        written.append(target)
    return written


def fetch_from_remote(ssh_host: str, remote_exporter: str, timeout: int = 35) -> dict[str, Any]:
    """Run remote read-only exporter with communicate() timeout (no pipe deadlock) & byte cap."""
    if not ssh_host:
        raise RuntimeError("请通过 --ssh-host 或环境变量 DANCLUB_SSH_HOST 指定数据源主机")
    proc = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_host, f"python3 {remote_exporter} --stdout"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        out_bytes, err_bytes = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("SSH 导出超时")
    if proc.returncode != 0:
        err_txt = _redact_repo_url(err_bytes.decode("utf-8", errors="replace").strip())[:240]
        raise RuntimeError(f"SSH 导出失败 (rc={proc.returncode}): {err_txt}")
    if len(out_bytes) > MAX_REMOTE_BYTES:
        raise RuntimeError(f"导出 JSON 体积超限 (>{MAX_REMOTE_BYTES} 字节)，拒绝处理")
    return json.loads(out_bytes.decode("utf-8"))


def _copy_whitelisted_static_file(src_root: Path, dst_root: Path, rel: str) -> None:
    # Verify every path segment under src_root is free of symlinks, then open with O_NOFOLLOW
    cur = src_root
    parts = Path(rel).parts
    for seg in parts[:-1]:
        cur = cur / seg
        st_d = os.lstat(cur)
        if stat.S_ISLNK(st_d.st_mode) or not stat.S_ISDIR(st_d.st_mode):
            raise RuntimeError(f"拒绝符号链接或非目录父路径: {rel}")
    src = src_root / rel
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(src), flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError(f"拒绝发布非普通文件: {rel}")
        with os.fdopen(fd, "rb") as rf:
            fd = -1
            data = rf.read()
    finally:
        if fd >= 0:
            os.close(fd)
    dst = dst_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(dst, data)


def publish_gh_pages(
    site_dir: Path,
    api_bytes: dict[str, bytes],
    repo_url: str,
    branch: str = "gh-pages",
    *,
    dry_run_stage_only: bool = False,
) -> dict[str, str]:
    """Stage ONLY whitelisted frontend files + exact verified API bytes, verify digests & file list, then push."""
    expected_digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in api_bytes.items()}
    expected_git_files = sorted(
        [*WHITELISTED_STATIC_FILES, ".nojekyll", *(f"api/v1/{name}" for name in api_bytes)]
    )

    with tempfile.TemporaryDirectory(prefix="danclub_pages.") as td:
        stage = Path(td) / "stage"
        stage.mkdir(mode=0o700)

        for rel in WHITELISTED_STATIC_FILES:
            _copy_whitelisted_static_file(site_dir, stage, rel)

        stage_api = stage / "api" / "v1"
        write_api_bytes(api_bytes, stage_api)
        (stage / ".nojekyll").write_text("", encoding="utf-8")

        # Verify every staged JSON matches in-memory verified SHA-256 byte-for-byte
        for name, exp_sha in expected_digests.items():
            actual_sha = hashlib.sha256((stage_api / name).read_bytes()).hexdigest()
            if actual_sha != exp_sha:
                raise RuntimeError(f"暂存文件摘要不一致: {name}")

        def run_git(*git_args: str) -> str:
            res = subprocess.run(
                ["git", *git_args],
                cwd=str(stage),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if res.returncode != 0:
                safe_cmd = " ".join(_redact_repo_url(a) for a in git_args)
                safe_err = _redact_repo_url(res.stderr.strip())[:240]
                raise RuntimeError(f"git {safe_cmd} failed: {safe_err}")
            return res.stdout

        run_git("init", "-b", branch)
        run_git("config", "user.name", "danclub-open")
        run_git("config", "user.email", "360107477+danclub-open@users.noreply.github.com")
        run_git("add", "-A")

        staged_lines = [ln for ln in run_git("ls-files", "--stage").splitlines() if ln.strip()]
        staged_files = []
        for ln in staged_lines:
            meta_part, fpath = ln.split("\t", 1)
            mode = meta_part.split()[0]
            if mode != "100644":
                raise RuntimeError(f"暂存文件权限/类型非普通文件 ({mode}): {fpath}")
            staged_files.append(fpath)
        staged_files.sort()
        if staged_files != expected_git_files:
            raise RuntimeError(f"暂存文件清单与白名单不符: {staged_files} != {expected_git_files}")

        run_git("commit", "-m", "chore(data): update sanitized public market snapshot")
        if not dry_run_stage_only:
            run_git("remote", "add", "origin", repo_url)
            run_git("push", "--force", "origin", f"{branch}:{branch}")
    return expected_digests


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync sanitized public market JSON to local / gh-pages")
    parser.add_argument("--ssh-host", default=DEFAULT_SSH_HOST, help="SSH host alias for read-only exporter")
    parser.add_argument("--remote-exporter", default=DEFAULT_REMOTE_EXPORTER, help="Remote exporter script path")
    parser.add_argument("--input", type=Path, help="Read bundle from local JSON file instead of SSH (testing)")
    parser.add_argument("--out-dir", type=Path, default=API_DIR, help="Output directory for local /api/v1/*.json")
    parser.add_argument("--blocklist", type=Path, default=None, help="Optional local private blocklist file")
    parser.add_argument("--require-blocklist", action="store_true", help="Fail closed if private blocklist is missing")
    parser.add_argument("--dry-run", action="store_true", help="Validate & print summary without writing files or pushing")
    parser.add_argument("--publish", action="store_true", help="Push verified whitelisted snapshot to gh-pages")
    parser.add_argument("--repo-url", default="git@github.com:danclub-open/danclub-terminal.git", help="Git remote URL")
    args = parser.parse_args()

    lock_fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("SKIP: another sync_public_data process is running")
            return 0

        raw = (
            json.loads(args.input.read_text(encoding="utf-8"))
            if args.input
            else fetch_from_remote(args.ssh_host, args.remote_exporter)
        )
        bundle = _clean_bundle(raw, blocklist_path=args.blocklist)
        if args.require_blocklist:
            assert_clean_payload(bundle, blocklist_path=args.blocklist, require_blocklist=True)
        api_bytes = build_verified_api_bytes(bundle, blocklist_path=args.blocklist)

        bd = bundle["overview"]["bottom_dashboard"]
        etf_btc = bundle["flows"]["etf_flows"]["summary"].get("BTC", {})
        feed_cnt = len(bundle["feed"]["items"])
        print(
            f"PRIVACY_OK | generated={bundle['meta']['generated_at_bjt']} | "
            f"bottom={bd['green']}/{bd['total']} | btc_etf_5d={etf_btc.get('sum_5d_musd')}M | "
            f"csi={len(bundle['arb']['csi_basis'])} | oi={len(bundle['flows']['bn_oi_radar'])} | "
            f"promos={len(bundle['labs']['exchange_promos'])} | feed={feed_cnt}"
        )

        if args.dry_run:
            return 0

        written = write_api_bytes(api_bytes, args.out_dir)
        total_kb = sum(p.stat().st_size for p in written) / 1024.0
        print(f"WRITTEN_OK: {len(written)} files ({total_kb:.1f} KB) -> {args.out_dir}")

        if args.publish:
            digests = publish_gh_pages(SITE_DIR, api_bytes, args.repo_url)
            print(
                f"PUBLISHED_OK: gh-pages ({len(digests)} verified JSONs) -> {_redact_repo_url(args.repo_url)}"
            )
        return 0
    except PrivacyViolationError as exc:
        print(f"BLOCKED_BY_PRIVACY_FIREWALL: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {_redact_repo_url(str(exc))}", file=sys.stderr)
        return 1
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
