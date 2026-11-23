#!/usr/bin/env python3
"""One-Way Public Data Sync & Multi-Target Static Publisher for DanClub Open Terminal.

Architecture (Strict One-Way Zero-Trust):
  upstream read-only exporter stdout (bounded byte read)
  -> local strict schema + bounded int checks + fail-closed privacy_guard firewall
  -> verified in-memory bytes staged cleanly with content-hash cache-busting on assets
  -> SHA-256 digest & exact whitelist verification
  -> optional orphan push to GitHub Pages (gh-pages) and/or atomic SSH static web root deploy.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
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
from whale_public_exporter import empty_whales_payload, export_whales  # noqa: E402

SITE_DIR = REPO_ROOT / "site"
API_DIR = SITE_DIR / "api" / "v1"
LOCK_FILE = Path("/tmp/danclub_open_sync.lock")
GH_STAMP_FILE = Path("/tmp/danclub_open_gh_last_push.txt")
DEFAULT_SSH_HOST = os.environ.get("DANCLUB_SSH_HOST", "")
DEFAULT_REMOTE_EXPORTER = os.environ.get("DANCLUB_REMOTE_EXPORTER", "./public_terminal_exporter.py")
DEFAULT_STATIC_TARGET = os.environ.get("DANCLUB_STATIC_DEPLOY_TARGET", "")
DEFAULT_WHALE_DB = os.environ.get("DANCLUB_WHALE_DB", "")
REQUIRED_TOP_KEYS = ("meta", "overview", "arb", "flows", "labs", "feed")
MAX_REMOTE_BYTES = 5 * 1024 * 1024

WHITELISTED_STATIC_FILES: tuple[str, ...] = (
    "index.html",
    "assets/style.css",
    "assets/app.js",
    "assets/war_arena.js",
)
ALLOWED_API_FILENAMES: frozenset[str] = frozenset(
    {"index.json", "overview.json", "arb.json", "flows.json", "labs.json", "feed.json", "whales.json"}
)

_SAFE_REMOTE_HOST_RE = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")
_SAFE_REMOTE_DIR_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,160}$")


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
    for sig in bundle["overview"].get("desk_signals", []):
        if isinstance(sig, dict):
            sig["title"] = sanitize_text(sig.get("title"), max_len=80)
            sig["detail"] = sanitize_text(sig.get("detail"), max_len=140)

    # 2. Arb strict integer/type validation
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
        it["headline"] = sanitize_text(it.get("headline"), max_len=190)
        it["body"] = sanitize_text(it.get("body"), max_len=240)
        it["source"] = sanitize_text(it.get("source"), max_len=40)
        it["impact_label"] = sanitize_text(it.get("impact_label"), max_len=80)
        it["market_ref"] = sanitize_text(it.get("market_ref"), max_len=120)
        if it["headline"]:
            clean_items.append(it)
    feed["items"] = clean_items[:40]

    # 4. Promos URL allowlist + text sanitization
    labs = bundle["labs"]
    clean_promos = []
    for pr in labs.get("exchange_promos", []):
        if not isinstance(pr, dict):
            continue
        pr["title"] = sanitize_text(pr.get("title"), max_len=120)
        pr["summary"] = sanitize_text(pr.get("summary"), max_len=180)
        pr["reward"] = sanitize_text(pr.get("reward"), max_len=120)
        pr["window"] = sanitize_text(pr.get("window"), max_len=80)
        pr["url"] = sanitize_public_url(pr.get("url"), allowed_domains=ALLOWED_PROMO_DOMAINS)
        if pr["title"]:
            clean_promos.append(pr)
    labs["exchange_promos"] = clean_promos[:12]

    # 4b. Whales section strict type/text validation (optional 7th public endpoint)
    whales = bundle.get("whales")
    if whales is None:
        whales = empty_whales_payload()
        bundle["whales"] = whales
    if not isinstance(whales, dict):
        raise ValueError("whales 段类型错误")
    w_stats = whales.get("stats") or {}
    for k in ("tracked_addresses", "profiled_whales", "active_entities"):
        _require_bounded_int(w_stats.get(k, 0), f"whales.stats.{k}", 0, 10_000_000)
    for i, fl in enumerate(whales.get("flagships", [])):
        if not isinstance(fl, dict):
            raise ValueError(f"whales.flagships[{i}] 不是对象")
        fl["display"] = sanitize_text(fl.get("display"), max_len=40)
        fl["short_addr"] = sanitize_text(fl.get("short_addr"), max_len=24)
        _require_bounded_int(fl.get("addr_count", 1), f"whales.flagships[{i}].addr_count", 1, 1000)

    # Inject Smart-vs-Counter extreme whale divergence signal into Overview desk_signals (top-of-screen hook!)
    btc_coh = (whales.get("cohorts") or {}).get("BTC") or {}
    div = btc_coh.get("divergence")
    if isinstance(div, dict) and div.get("spread_pct") is not None and abs(float(div["spread_pct"])) >= 30.0:
        sa = btc_coh.get("SMART_SA") or {}
        cc = btc_coh.get("COUNTER_C") or {}
        whale_sig = {
            "severity": "danger" if float(div["spread_pct"]) < 0 else "opportunity",
            "tag": "巨鲸阵营背离",
            "title": "BTC 聪明钱 vs 反指巨鲸对撞",
            "metric": f"差值 {float(div['spread_pct']):+.1f}%",
            "detail": f"S级聪明钱 {sa.get('notional_long_pct', 0):.0f}%多 vs C级反指 {cc.get('notional_long_pct', 0):.0f}%多 · 点击进空战战情室",
            "target_tab": "war",
        }
        sigs = bundle["overview"].get("desk_signals")
        if isinstance(sigs, list):
            sigs = [s for s in sigs if isinstance(s, dict) and s.get("tag") != "巨鲸阵营背离"]
            bundle["overview"]["desk_signals"] = [whale_sig, *sigs][:5]

    # Ensure endpoints meta advertises whales.json
    if isinstance(bundle["meta"].get("endpoints"), dict):
        bundle["meta"]["endpoints"]["whales"] = "./api/v1/whales.json"

    # 5. Fail-closed privacy firewall across the entire payload
    assert_clean_payload(bundle, blocklist_path=blocklist_path)
    return bundle


def build_verified_api_bytes(bundle: dict[str, Any], *, blocklist_path: Path | None = None) -> dict[str, bytes]:
    """Serialize all 7 split API payloads in memory with strict allow_nan=False and privacy check."""
    meta = bundle["meta"]
    outputs = {
        "index.json": meta,
        "overview.json": {"meta": meta, **bundle["overview"]},
        "arb.json": {"meta": meta, **bundle["arb"]},
        "flows.json": {"meta": meta, **bundle["flows"]},
        "labs.json": {"meta": meta, **bundle["labs"]},
        "feed.json": {"meta": meta, **bundle["feed"]},
        "whales.json": {"meta": meta, **(bundle.get("whales") or empty_whales_payload())},
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
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_host, f"python3 {shlex.quote(remote_exporter)} --stdout"],
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


def _read_whitelisted_static_file(src_root: Path, rel: str) -> bytes:
    """Read a whitelisted static file via dir_fd traversal + O_NOFOLLOW to close TOCTOU symlink windows."""
    o_nofollow = getattr(os, "O_NOFOLLOW", 0)
    o_dir = getattr(os, "O_DIRECTORY", 0)
    root_st = os.lstat(src_root)
    if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
        raise RuntimeError("拒绝符号链接或非目录源根目录")
    dir_fd = os.open(str(src_root), os.O_RDONLY | o_dir | o_nofollow)
    try:
        parts = Path(rel).parts
        for seg in parts[:-1]:
            if seg in ("", ".", ".."):
                raise RuntimeError(f"非法路径分量: {rel}")
            nxt_fd = os.open(seg, os.O_RDONLY | o_dir | o_nofollow, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = nxt_fd
        fname = parts[-1]
        if fname in ("", ".", ".."):
            raise RuntimeError(f"非法文件名: {rel}")
        fd = os.open(fname, os.O_RDONLY | o_nofollow, dir_fd=dir_fd)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise RuntimeError(f"拒绝发布非普通文件: {rel}")
            with os.fdopen(fd, "rb") as rf:
                fd = -1
                return rf.read()
        finally:
            if fd >= 0:
                os.close(fd)
    finally:
        os.close(dir_fd)


def build_verified_release_bundle(site_dir: Path, api_bytes: dict[str, bytes]) -> dict[str, bytes]:
    """Assemble exact whitelisted static + API bytes with content-hash cache-busting in index.html."""
    files: dict[str, bytes] = {}
    for rel in WHITELISTED_STATIC_FILES:
        files[rel] = _read_whitelisted_static_file(site_dir, rel)

    css_hash = hashlib.sha256(files["assets/style.css"]).hexdigest()[:10]
    js_hash = hashlib.sha256(files["assets/app.js"]).hexdigest()[:10]
    war_hash = hashlib.sha256(files["assets/war_arena.js"]).hexdigest()[:10]
    html = files["index.html"].decode("utf-8")
    html = re.sub(r'(\./assets/style\.css)(?:\?v=[A-Za-z0-9._-]+)?', rf'\1?v={css_hash}', html)
    html = re.sub(r'(\./assets/app\.js)(?:\?v=[A-Za-z0-9._-]+)?', rf'\1?v={js_hash}', html)
    html = re.sub(r'(\./assets/war_arena\.js)(?:\?v=[A-Za-z0-9._-]+)?', rf'\1?v={war_hash}', html)
    files["index.html"] = html.encode("utf-8")

    for name, raw in api_bytes.items():
        if name not in ALLOWED_API_FILENAMES:
            raise ValueError(f"非白名单 API 文件名: {name}")
        files[f"api/v1/{name}"] = raw
    return files


def publish_gh_pages(
    site_dir: Path,
    api_bytes: dict[str, bytes],
    repo_url: str,
    branch: str = "gh-pages",
    *,
    dry_run_stage_only: bool = False,
) -> dict[str, str]:
    """Stage ONLY whitelisted frontend files + exact verified API bytes, verify digests & file list, then push."""
    release_files = build_verified_release_bundle(site_dir, api_bytes)
    expected_digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in api_bytes.items()}
    expected_git_files = sorted([*release_files.keys(), ".nojekyll"])

    with tempfile.TemporaryDirectory(prefix="danclub_pages.") as td:
        stage = Path(td) / "stage"
        stage.mkdir(mode=0o700)

        for rel, data in release_files.items():
            _atomic_write_bytes(stage / rel, data)
        (stage / ".nojekyll").write_text("", encoding="utf-8")

        for name, exp_sha in expected_digests.items():
            actual_sha = hashlib.sha256((stage / "api" / "v1" / name).read_bytes()).hexdigest()
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


def parse_static_ssh_target(target: str) -> tuple[str, str]:
    """Validate and split 'ssh_host:/absolute/webroot' strictly."""
    if ":" not in target:
        raise ValueError("静态部署目标格式必须为 <ssh_host>:/absolute/path")
    host, remote_dir = target.split(":", 1)
    host = host.strip()
    remote_dir = remote_dir.strip().rstrip("/")
    if not _SAFE_REMOTE_HOST_RE.match(host) or host.startswith("-"):
        raise ValueError(f"非法的远程 SSH 主机名: {host}")
    if not _SAFE_REMOTE_DIR_RE.match(remote_dir) or ".." in remote_dir.split("/") or remote_dir in ("/", "/etc", "/root", "/home", "/usr", "/var"):
        raise ValueError(f"非法的远程静态目录路径: {remote_dir}")
    return host, remote_dir


def build_static_tar_bytes(release_files: dict[str, bytes]) -> bytes:
    """Build deterministic gzip-compressed tar archive of whitelisted 0644 regular files only."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        for rel in sorted(release_files.keys()):
            if rel.startswith("/") or ".." in Path(rel).parts:
                raise ValueError(f"非法归档路径: {rel}")
            raw = release_files[rel]
            ti = tarfile.TarInfo(name=rel)
            ti.size = len(raw)
            ti.mode = 0o644
            ti.mtime = int(time.time())
            tf.addfile(ti, io.BytesIO(raw))
    return buf.getvalue()


def publish_static_ssh(
    site_dir: Path,
    api_bytes: dict[str, bytes],
    target: str,
    *,
    dry_run_stage_only: bool = False,
) -> dict[str, str]:
    """Atomically deploy verified whitelisted static + API bundle to a remote web server via SSH."""
    host, remote_dir = parse_static_ssh_target(target)
    release_files = build_verified_release_bundle(site_dir, api_bytes)
    digests = {rel: hashlib.sha256(raw).hexdigest() for rel, raw in release_files.items()}
    manifest_bytes = "".join(f"{sha}  {rel}\n" for rel, sha in sorted(digests.items())).encode("utf-8")
    tar_files = {**release_files, ".manifest.sha256": manifest_bytes}
    tar_bytes = build_static_tar_bytes(tar_files)
    if dry_run_stage_only:
        return digests

    # Remote script rejects symlinks on target paths, verifies SHA-256 of EVERY extracted file, then installs cleanly
    q_dir = shlex.quote(remote_dir)
    q_parent = shlex.quote(str(Path(remote_dir).parent))
    remote_cmd = (
        f"set -eu; "
        f"test ! -L {q_parent}; "
        f"if [ -e {q_dir} ]; then test -d {q_dir} && test ! -L {q_dir}; else mkdir -p {q_dir}; fi; "
        f"for sub in assets api api/v1; do "
        f"  if [ -e {q_dir}/\"$sub\" ]; then test -d {q_dir}/\"$sub\" && test ! -L {q_dir}/\"$sub\"; else mkdir -p {q_dir}/\"$sub\"; fi; "
        f"done; "
        f"tmp=$(mktemp -d {q_dir}/.stage.XXXXXX); "
        f"trap 'rm -rf \"$tmp\"' EXIT; "
        f"tar -xzf - --no-same-owner --no-same-permissions -C \"$tmp\"; "
        f"(cd \"$tmp\" && sha256sum -c .manifest.sha256 >/dev/null); "
        f"chmod 0644 \"$tmp/index.html\" \"$tmp/assets/\"* \"$tmp/api/v1/\"*; "
        f"for rel in index.html assets/style.css assets/app.js assets/war_arena.js; do test ! -L {q_dir}/\"$rel\"; done; "
        f"for f in \"$tmp/api/v1/\"*.json; do test ! -L {q_dir}/api/v1/\"$(basename \"$f\")\"; done; "
        f"mv -f \"$tmp/assets/style.css\" {q_dir}/assets/style.css; "
        f"mv -f \"$tmp/assets/app.js\" {q_dir}/assets/app.js; "
        f"mv -f \"$tmp/assets/war_arena.js\" {q_dir}/assets/war_arena.js; "
        f"for f in \"$tmp/api/v1/\"*.json; do mv -f \"$f\" {q_dir}/api/v1/\"$(basename \"$f\")\"; done; "
        f"mv -f \"$tmp/index.html\" {q_dir}/index.html"
    )
    proc = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, remote_cmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _, err_b = proc.communicate(input=tar_bytes, timeout=45)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("远程静态站点 SSH 部署超时")
    if proc.returncode != 0:
        err_txt = _redact_repo_url(err_b.decode("utf-8", errors="replace").strip())[:240]
        raise RuntimeError(f"远程静态站点部署失败 (rc={proc.returncode}): {err_txt}")
    return digests


def _should_push_gh(min_interval: int) -> bool:
    if min_interval <= 0:
        return True
    try:
        last = float(GH_STAMP_FILE.read_text(encoding="utf-8").strip())
        return (time.time() - last) >= min_interval
    except (OSError, ValueError):
        return True


def _record_gh_push() -> None:
    try:
        GH_STAMP_FILE.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync sanitized public market JSON to local / gh-pages / static SSH")
    parser.add_argument("--ssh-host", default=DEFAULT_SSH_HOST, help="SSH host alias for read-only exporter")
    parser.add_argument("--remote-exporter", default=DEFAULT_REMOTE_EXPORTER, help="Remote exporter script path")
    parser.add_argument("--input", type=Path, help="Read bundle from local JSON file instead of SSH (testing)")
    parser.add_argument("--out-dir", type=Path, default=API_DIR, help="Output directory for local /api/v1/*.json")
    parser.add_argument("--blocklist", type=Path, default=None, help="Optional local private blocklist file")
    parser.add_argument("--require-blocklist", action="store_true", help="Fail closed if private blocklist is missing")
    parser.add_argument("--dry-run", action="store_true", help="Validate & print summary without writing files or pushing")
    parser.add_argument("--publish", action="store_true", help="Push verified whitelisted snapshot to gh-pages")
    parser.add_argument("--gh-min-interval", type=int, default=0, help="Min seconds between gh-pages pushes (0=every run)")
    parser.add_argument("--static-ssh-target", default=DEFAULT_STATIC_TARGET, help="Optional SSH static deploy host:/dir")
    parser.add_argument("--whale-db", default=DEFAULT_WHALE_DB, help="Optional local read-only whale_intel.db path")
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
        if args.whale_db:
            raw["whales"] = export_whales(Path(args.whale_db).expanduser())
        bundle = _clean_bundle(raw, blocklist_path=args.blocklist)
        if args.require_blocklist:
            assert_clean_payload(bundle, blocklist_path=args.blocklist, require_blocklist=True)
        api_bytes = build_verified_api_bytes(bundle, blocklist_path=args.blocklist)

        bd = bundle["overview"]["bottom_dashboard"]
        etf_btc = bundle["flows"]["etf_flows"]["summary"].get("BTC", {})
        feed_cnt = len(bundle["feed"]["items"])
        sig_cnt = len(bundle["overview"].get("desk_signals", []))
        whale_cnt = len((bundle.get("whales") or {}).get("flagships", []))
        print(
            f"PRIVACY_OK | generated={bundle['meta']['generated_at_bjt']} | "
            f"bottom={bd['green']}/{bd['total']} | signals={sig_cnt} | whales={whale_cnt} | btc_etf_5d={etf_btc.get('sum_5d_musd')}M | "
            f"csi={len(bundle['arb']['csi_basis'])} | oi={len(bundle['flows']['bn_oi_radar'])} | "
            f"promos={len(bundle['labs']['exchange_promos'])} | feed={feed_cnt}"
        )

        if args.dry_run:
            return 0

        written = write_api_bytes(api_bytes, args.out_dir)
        total_kb = sum(p.stat().st_size for p in written) / 1024.0
        print(f"WRITTEN_OK: {len(written)} files ({total_kb:.1f} KB) -> {args.out_dir}")

        if args.static_ssh_target:
            static_digests = publish_static_ssh(SITE_DIR, api_bytes, args.static_ssh_target)
            print(f"STATIC_DEPLOY_OK: {len(static_digests)} verified files synced")

        if args.publish:
            if _should_push_gh(args.gh_min_interval):
                digests = publish_gh_pages(SITE_DIR, api_bytes, args.repo_url)
                _record_gh_push()
                print(
                    f"PUBLISHED_OK: gh-pages ({len(digests)} verified JSONs) -> {_redact_repo_url(args.repo_url)}"
                )
            else:
                print(f"GH_PAGES_SKIPPED: within min interval ({args.gh_min_interval}s)")
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
