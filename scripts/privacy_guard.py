#!/usr/bin/env python3
"""Fail-closed Privacy & Secret Firewall for DanClub Open Terminal.

Runs BEFORE any public JSON file is written or published.
- Public source contains ONLY generic security rules (zero deployment-specific literals).
- Optional local private blocklist file (~/.secrets/danclub_privacy_blocklist.txt) is
  loaded at runtime on the publishing host to block host-specific IPs/IDs/names.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class PrivacyViolationError(RuntimeError):
    """Raised when forbidden private/secret content is detected in public output."""


DEFAULT_LOCAL_BLOCKLIST = Path.home() / ".secrets" / "danclub_privacy_blocklist.txt"

# Generic security & PII patterns (safe to open-source; zero host-specific identifiers)
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("ssh_public_key", re.compile(r"\bssh-(?:rsa|ed25519|ecdsa)\s+[A-Za-z0-9+/=]{20,}")),
    ("openai_or_sk_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt_token", re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b")),
    (
        "personal_email",
        re.compile(
            r"(?i)\b[a-z0-9._%+-]+@(?!users\.noreply\.github\.com\b)"
            r"(?:163|126|qq|gmail|icloud|outlook|hotmail|sina|sohu)\.com\b"
        ),
    ),
    ("cn_mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    (
        "private_ipv4",
        re.compile(
            r"(?<!\d)(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|127\.0\.0\.1)(?!\d)"
        ),
    ),
    ("local_fs_path", re.compile(r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+/|\.secrets/|totp_vault)")),
    ("private_marker", re.compile(r"(?:微信私聊|私聊主人|早报草稿|个人持仓|我的仓位|我的成本|实盘账户余额)")),
]

STRIP_LINE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"💡?\s*群内可\s*@[^\n]*"),
    re.compile(r"回复\s*@[^\n]*"),
    re.compile(r"（仅群内[^）]*）"),
    re.compile(r"【内部[^】]*】"),
]

# Official exchange & public wire hostnames allowed for public promo/news/profile links (subdomain-aware)
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
    "asterdex.com",
    "eqn.news",
    "x.com",
)
ALLOWED_NEWS_DOMAINS: tuple[str, ...] = ALLOWED_PROMO_DOMAINS + (
    "upbit.com",
    "bithumb.com",
    "sec.gov",
    "reuters.com",
    "bloomberg.com",
    "coindesk.com",
    "theblock.co",
)


def load_extra_blocklist(
    path: Path | None = None,
    *,
    require_exists: bool = False,
) -> list[tuple[str, re.Pattern[str]]]:
    """Load deployment-specific literal blocklist from a local private file (outside git)."""
    env_p = os.environ.get("DANCLUB_PRIVACY_BLOCKLIST", "").strip()
    target = path if path is not None else (Path(env_p) if env_p else DEFAULT_LOCAL_BLOCKLIST)
    if not target.is_file():
        if require_exists:
            raise PrivacyViolationError("必需的本机私有黑名单文件不存在，拒绝发布")
        return []
    st = os.lstat(target)
    if st.st_uid != os.getuid() or (st.st_mode & 0o077):
        raise PrivacyViolationError("私有黑名单文件属主或权限不安全（须为当前用户 0600），拒绝发布")
    extra: list[tuple[str, re.Pattern[str]]] = []
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        extra.append(("local_blocklist", re.compile(re.escape(line), re.IGNORECASE)))
    return extra


def _host_matches_allowed(host: str, allowed_domains: tuple[str, ...]) -> bool:
    h = host.lower().rstrip(".")
    for dom in allowed_domains:
        if h == dom or h.endswith("." + dom):
            return True
    return False


def sanitize_public_url(url: str | None, *, allowed_domains: tuple[str, ...] | None = None) -> str:
    """Allow only clean https:// URLs, reject userinfo/private hosts, and strip query strings."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return ""
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1") or host.startswith(("192.168.", "10.")):
        return ""
    if allowed_domains is not None and not _host_matches_allowed(host, allowed_domains):
        return ""
    # Strip query and fragment completely to eliminate referral codes, tokens, or signed params
    return parsed._replace(query="", params="", fragment="").geturl()


def sanitize_text(text: str | None, *, max_len: int = 420) -> str:
    """Clean internal group prompt hints and normalize whitespace."""
    if not text or not isinstance(text, str):
        return ""
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    for pat in STRIP_LINE_PATTERNS:
        out = pat.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if len(out) > max_len:
        out = out[: max_len - 1].rstrip() + "…"
    return out


def _scan_string(val: str, path: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    violations: list[str] = []
    low = val.strip().lower()
    if low.startswith(("javascript:", "vbscript:", "file://", "data:text/html")):
        violations.append(f"{path}: forbidden URI scheme")
    for label, pat in patterns:
        if pat.search(val):
            violations.append(f"{path}: matched forbidden pattern '{label}'")
    return violations


def scan_payload(
    obj: Any,
    path: str = "$",
    extra_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
) -> list[str]:
    """Recursively scan BOTH keys and values of a JSON-serializable payload."""
    patterns = FORBIDDEN_PATTERNS + (extra_patterns or [])
    violations: list[str] = []
    if isinstance(obj, dict):
        for idx, (k, v) in enumerate(obj.items()):
            ks = str(k)
            safe_path = f"{path}.<key#{idx}>"
            if any(tok in ks.lower() for tok in ("secret", "password", "passwd", "auth_code", "private_key", "cookie", "token")):
                violations.append(f"{safe_path}: forbidden key name")
            violations.extend(_scan_string(ks, safe_path, patterns))
            violations.extend(scan_payload(v, f"{path}.{ks if len(ks) <= 24 and ks.isidentifier() else f'<key#{idx}>'}", extra_patterns))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            violations.extend(scan_payload(item, f"{path}[{i}]", extra_patterns))
    elif isinstance(obj, str):
        violations.extend(_scan_string(obj, path, patterns))
    return violations


def assert_clean_payload(
    obj: Any,
    *,
    blocklist_path: Path | None = None,
    require_blocklist: bool = False,
) -> None:
    """Fail-closed check: raises PrivacyViolationError on any finding."""
    extra = load_extra_blocklist(blocklist_path, require_exists=require_blocklist)
    violations = scan_payload(obj, extra_patterns=extra)
    if violations:
        sample = "; ".join(violations[:8])
        raise PrivacyViolationError(f"隐私/安全防火墙拦截发布（共 {len(violations)} 项）: {sample}")
