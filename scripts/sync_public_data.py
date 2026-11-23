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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote as url_quote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from privacy_guard import (  # noqa: E402
    ALLOWED_NEWS_DOMAINS,
    ALLOWED_PROMO_DOMAINS,
    PrivacyViolationError,
    assert_clean_payload,
    sanitize_public_url,
    sanitize_text,
)
from whale_public_exporter import empty_whales_payload, export_whales  # noqa: E402

BJT = timezone(timedelta(hours=8))

# Official Binance Spot bStocks (*BUSDT) mapping for 24/7 US equity pricing during NYSE/Nasdaq off-hours
BSTOCK_SYMBOL_MAP: dict[str, tuple[str, str]] = {
    "SPY": ("SPYBUSDT", "标普500 ETF"),
    "QQQ": ("QQQBUSDT", "纳指100 ETF"),
    "NVDA": ("NVDABUSDT", "英伟达"),
    "MU": ("MUBUSDT", "美光科技"),
    "SNDK": ("SNDKBUSDT", "闪迪 SanDisk"),
    "MSTR": ("MSTRBUSDT", "Strategy"),
    "COIN": ("COINBUSDT", "Coinbase"),
    "CRCL": ("CRCLBUSDT", "Circle"),
    "HOOD": ("HOODBUSDT", "Robinhood"),
    "TSLA": ("TSLABUSDT", "特斯拉"),
    "AAPL": ("AAPLBUSDT", "苹果"),
    "GOOGL": ("GOOGLBUSDT", "谷歌"),
    "TSM": ("TSMBUSDT", "台积电"),
    "ANTB": ("ANTBUSDT", "Anthropic bStock"),
}
BSTOCK_EXTRA_ORDER: tuple[str, ...] = ("TSLA", "AAPL", "GOOGL", "TSM", "ANTB")

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
    "assets/audio/valkyries.mp3",
    "assets/audio/1812_cannon.mp3",
    "assets/audio/william_tell.mp3",
    "assets/audio/toreador.mp3",
    "assets/audio/mars_war.mp3",
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


def _http_get_json(url: str, *, timeout: int = 8, max_bytes: int = 2 * 1024 * 1024) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DanClubTerminalSync/1.0 (https://github.com/danclub-open/danclub-terminal)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(f"HTTP 响应体积超限: {url}")
        return json.loads(raw.decode("utf-8"))


def fetch_binance_bstocks(timeout: int = 8) -> dict[str, dict[str, Any]]:
    """Fetch 24/7 Binance Spot bStocks (*BUSDT) prices from data-api.binance.vision (fail-soft)."""
    wanted_pairs = {pair: sym for sym, (pair, _) in BSTOCK_SYMBOL_MAP.items()}
    try:
        rows = _http_get_json(
            "https://data-api.binance.vision/api/v3/ticker/24hr",
            timeout=timeout,
            max_bytes=4 * 1024 * 1024,
        )
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        pair = str(r.get("symbol") or "")
        sym = wanted_pairs.get(pair)
        if not sym:
            continue
        try:
            px = float(r.get("lastPrice") or 0)
            chg = float(r.get("priceChangePercent") or 0)
            vol = float(r.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            continue
        if px > 0 and vol >= 10_000:
            out[sym] = {
                "bstock_symbol": pair,
                "price": round(px, 4 if px < 10 else 2),
                "chg_pct": round(chg, 2),
                "vol_usd": round(vol, 0),
            }
    return out


def apply_bstocks_overlay(bundle: dict[str, Any], bstocks: dict[str, dict[str, Any]]) -> None:
    """Promote Binance Spot bStocks 24/7 quotes as primary price during US equity off-hours."""
    if not bstocks:
        return
    ov = bundle.get("overview")
    if not isinstance(ov, dict):
        return
    quotes = ov.get("macro_quotes")
    if not isinstance(quotes, list):
        return
    now_ts = int(time.time())
    existing_syms: set[str] = set()
    any_promoted = False
    equity_groups = {"美股指数", "算力存储", "币股", "科技巨头", "EQUITY"}
    off_hours_sessions = {"CLOSED", "PRE", "POST"}
    eq_sessions = [
        str(q.get("session") or "").upper()
        for q in quotes
        if isinstance(q, dict) and str(q.get("group") or "") in equity_groups
    ]
    us_equity_all_off_hours = bool(eq_sessions) and all(s in off_hours_sessions for s in eq_sessions)

    for q in quotes:
        if not isinstance(q, dict):
            continue
        sym = str(q.get("symbol") or "")
        existing_syms.add(sym)
        bs = bstocks.get(sym)
        if not bs or str(q.get("group") or "") not in equity_groups:
            continue
        q["bstock_symbol"] = bs["bstock_symbol"]
        q["bstock_px"] = bs["price"]
        q["bstock_chg_pct"] = bs["chg_pct"]
        q["bstock_vol_usd"] = bs["vol_usd"]
        sess_u = str(q.get("session") or "").upper()
        # STRICT: Only promote Binance Spot bStocks when US equity is explicitly in CLOSED/PRE/POST off-hours!
        if sess_u in off_hours_sessions:
            if q.get("reg_px") is None and q.get("price") is not None:
                q["reg_px"] = q["price"]
            if q.get("reg_chg_pct") is None and q.get("chg_pct") is not None:
                q["reg_chg_pct"] = q["chg_pct"]
            q["price"] = bs["price"]
            q["chg_pct"] = bs["chg_pct"]
            q["session"] = "BSTOCKS_247"
            q["is_closed"] = False
            q["price_source"] = "BINANCE_BSTOCKS"
            any_promoted = True

    # Only append extra 24/7 bStocks cards (TSLA/AAPL/GOOGL/TSM/ANTB) when ALL tracked US equities are off-hours!
    if us_equity_all_off_hours:
        for sym in BSTOCK_EXTRA_ORDER:
            if sym in existing_syms or sym not in bstocks:
                continue
            bs = bstocks[sym]
            _, zh_name = BSTOCK_SYMBOL_MAP[sym]
            quotes.append({
                "symbol": sym,
                "name": zh_name,
                "group": "科技巨头",
                "price": bs["price"],
                "chg_pct": bs["chg_pct"],
                "session": "BSTOCKS_247",
                "is_closed": False,
                "price_source": "BINANCE_BSTOCKS",
                "bstock_symbol": bs["bstock_symbol"],
                "bstock_px": bs["price"],
                "bstock_chg_pct": bs["chg_pct"],
                "bstock_vol_usd": bs["vol_usd"],
                "reg_px": None,
                "reg_chg_pct": None,
                "ext_px": None,
                "ext_chg": None,
                "dd_pct": None,
                "yrhi": None,
                "ts": now_ts,
            })
            any_promoted = True
    if any_promoted:
        ov["bstocks_active"] = True


def _norm_title_key(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(text or "").lower())[:32]


_MACRO_SOURCES = frozenset({
    "firstsquawk",
    "financialjuice",
    "reuters",
    "bloomberg",
    "wsj",
    "cnbc",
    "zerohedge",
    "deitaone",
    "walter bloomberg",
    "fxhedge",
    "unusual_whales",
})
_CRYPTO_EXPLICIT_RE = re.compile(
    r"(?:\b(?:BTC|ETH|SOL|BNB|XRP|HYPE|DOGE|USDT|USDC|RLUSD|BITCOIN|ETHEREUM|SOLANA|CRYPTO|DEFI|NFT|"
    r"BINANCE|COINBASE|OKX|BYBIT|BITGET|UPBIT|HYPERLIQUID|TETHER|CIRCLE|MICROSTRATEGY|MSTR|STABLECOIN)\b|"
    r"比特币|以太坊|加密|币圈|币安|稳定币|永续合约|链上|交易所|空投)",
    re.IGNORECASE,
)
_MACRO_KEYWORD_RE = re.compile(
    r"(?:美股|美联储|央行|降息|加息|通胀|非农|关税|国债|英伟达|特斯拉|纳指|标普|黄金|白银|原油|炼油|油管|伊朗|俄乌|白宫|"
    r"\b(?:FED|FOMC|POWELL|CPI|PCE|GDP|PAYROLLS?|TREASURY|YIELD|TARIFFS?|SANCTIONS?|OPEC|BRENT|WTI|"
    r"OIL|PIPELINE|REFINERY|GOLD|SILVER|NASDAQ|S&P|DOW|NVIDIA|NVDA|TESLA|TSLA|APPLE|AAPL|MSFT|GOOGL|"
    r"META|AMD|TSMC|SEMICONDUCTOR|MINISTER|SECRETARY OF STATE|SENATOR|CONGRESS|PARLIAMENT|ECB|BOJ|BOE|"
    r"UKRAINE|RUSSIA|LIBYA|ISRAEL|IRAN|SWITZERLAND|CHINA FOREIGN)\b)",
    re.IGNORECASE,
)
_INTEL_SECURITY_RE = re.compile(
    r"(?:漏洞|被盗|黑客|攻击|抽走|证监会|破产|清盘|暂停提现|挤兑|"
    r"\b(?:HACK|HACKED|EXPLOIT|DRAINED|BANKRUPTCY|INSOLVENCY|SEIZED|INDICTED)\b)",
    re.IGNORECASE,
)


def _classify_eqn_item(channels: list[str], text: str, src_name: str) -> tuple[str, str]:
    chs = set(channels)
    src_low = (src_name or "").strip().lower()
    if "earn" in chs or ("alpha" in chs and re.search(r"(?:空投|积分|认购|奖励|Yield|APR|年化|理财|Simple\s*Earn|瓜分)", text, re.IGNORECASE)):
        return "EARN", "理财·羊毛"
    if chs & {"binance", "korea", "tier2", "tier3", "alpha"}:
        return "LISTING", "上新·公告"
    if chs & {"tradfi", "tfmoves"}:
        return "MACRO", "美股·宏观"
    if chs & {"oi", "pricechange"}:
        return "MOVES", "异动·OI"
    # Case-sensitive word-boundary \bSEC\b so "Secretary" NEVER triggers INTEL!
    if _INTEL_SECURITY_RE.search(text) or re.search(r"\bSEC\b", text):
        return "INTEL", "深度预警"
    if src_low in _MACRO_SOURCES and not _CRYPTO_EXPLICIT_RE.search(text):
        return "MACRO", "美股·宏观"
    if _MACRO_KEYWORD_RE.search(text) and not _CRYPTO_EXPLICIT_RE.search(text):
        return "MACRO", "美股·宏观"
    return "CRYPTO", "加密快讯"


def _split_promo_title_summary(clean_head: str, src_name: str) -> tuple[str, str]:
    """Split long promo notices at natural sentence punctuation instead of mid-word."""
    if len(clean_head) <= 72:
        return clean_head, f"来源：{src_name}（点击查看原文规则）"
    for m in re.finditer(r"(?:[！。！？]|[!?](?:\s|$))", clean_head):
        pos = m.end()
        if 16 <= pos <= 95:
            t = clean_head[:pos].strip()
            s = clean_head[pos : pos + 160].strip()
            return t, s or f"来源：{src_name}（点击查看原文规则）"
    cut = max(clean_head.rfind("，", 20, 88), clean_head.rfind(", ", 20, 88), clean_head.rfind(" ", 30, 88))
    if cut >= 24:
        return clean_head[:cut].strip(), clean_head[cut:].lstrip("，, ").strip()[:160]
    return clean_head[:84].strip(), clean_head[84:240].strip() or f"来源：{src_name}（点击查看原文规则）"


def _norm_promo_url(url: str) -> str:
    """Normalize promo URL while preserving campaign-identifying query params (stripping only tracking params)."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import parse_qsl, urlencode
        p = urlparse(raw)
        if p.scheme in ("http", "https") and p.netloc:
            drop_keys = {"ref", "from", "spm", "source", "channel", "fbclid", "gclid"}
            kept = sorted(
                (k, v)
                for k, v in parse_qsl(p.query, keep_blank_values=False)
                if not k.lower().startswith("utm_") and k.lower() not in drop_keys
            )
            q = f"?{urlencode(kept)}" if kept else ""
            return f"{p.netloc.lower()}{p.path.rstrip('/')}{q}"
    except ValueError:
        pass
    return ""


def _promo_semantic_key(exchange: str, title: str, summary: str, earn_cat: str = "") -> str:
    """High-precision cross-source duplicate fingerprint: requires >=2 uppercase asset tickers + reward pool amount."""
    ex = (exchange or "").strip().lower()
    cat = (earn_cat or "").strip().upper()
    txt = f"{title} {summary}"
    non_asset_upper = {
        "VIP", "APR", "APY", "RWA", "USD", "NEW", "HOT", "PRO", "MAX", "APP", "WEB",
        "OKX", "BTC", "ETH", "BJT", "UTC", "TGE", "NFT", "DAO", "DEX", "CEX", "KYC", "API", "WSOT",
    }
    # Only match genuine ALL-UPPERCASE ticker symbols (e.g. RLUSD, XRP, STBL, ZEST, SXT, BERA)
    tickers = sorted({
        t for t in re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,8}(?![A-Za-z0-9])", txt)
        if t not in non_asset_upper
    })
    # Match concrete reward pool/quantity amounts (exclude generic % APY rates)
    pool_nums = sorted({
        n.replace(",", "")
        for n in re.findall(r"(?<!\d)\d[\d,]*(?:\.\d+)?(?:万|亿|M|K)(?!\d)", txt)
    })
    if len(tickers) >= 2 and pool_nums:
        return f"{ex}|{cat}|{'_'.join(tickers[:4])}|{'_'.join(pool_nums[:2])}"
    return ""


def _classify_earn_promo(title: str, summary: str, tier: str) -> tuple[str, str]:
    txt = f"{title} {summary}"
    if tier == "WARN" or re.search(r"(?:Trading\s+Competition|交易竞赛|刷量|交易量排名|反撸)", txt, re.IGNORECASE):
        return "WARN", "慎防反撸"
    if re.search(r"(?:Alpha\s*积分|空投|Airdrop|Pre-TGE|Launchpool|Megadrop|HODLer|打新|认购|领取\s*[\d,]+)", txt, re.IGNORECASE):
        return "AIRDROP", "空投·打新"
    if re.search(r"(?:APR|APY|年化|Simple\s*Earn|Yield\s*Arena|Hold\s*&\s*Earn|活期|理财|RWA|瓜分\s*[\d万亿]+|收益率)", txt, re.IGNORECASE):
        return "HIGH_YIELD", "高息理财"
    return "CAMPAIGN", "常规活动"


def fetch_eqn_news_and_earn(timeout: int = 8) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch categorized public market wires and live earn/airdrop promos from eqn.news (fail-soft)."""
    queries = (
        "main,tradfi,tfmoves,major,hl&limit=35",
        "binance,korea,tier2,alpha&limit=25",
        "earn,alpha&limit=24",
        "oi&limit=16",
    )
    now_ts = int(time.time())
    seen_ids: set[str] = set()
    feed_items: list[dict[str, Any]] = []
    earn_promos: list[dict[str, Any]] = []

    for q in queries:
        try:
            data = _http_get_json(f"https://eqn.news/api/v1/news?ch={q}", timeout=timeout, max_bytes=512 * 1024)
        except Exception:
            continue
        rows = data.get("items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        for it in rows:
            if not isinstance(it, dict):
                continue
            item_id = str(it.get("id") or it.get("no") or "")
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            chs = [str(c) for c in (it.get("channels") or []) if c]
            if "cz" in chs and len(chs) == 1:
                continue  # Skip noisy single-channel Twitter replies

            i18n = it.get("i18n") if isinstance(it.get("i18n"), dict) else {}
            raw_text = str(i18n.get("zh") or i18n.get("en") or "").strip()
            raw_text = re.sub(r"\s+", " ", raw_text).strip()
            if not raw_text or len(raw_text) < 10:
                continue
            if re.search(r"https?://t\.co/", raw_text) and len(raw_text) < 40:
                continue

            try:
                pub_raw = int(float(it.get("published_ts") or 0))
            except (TypeError, ValueError, OverflowError):
                continue
            ts_sec = pub_raw // 1000 if pub_raw > 10_000_000_000 else pub_raw
            if ts_sec <= 0 or ts_sec > now_ts + 3600 or (now_ts - ts_sec) > 7 * 86400:
                continue
            try:
                dt_str = datetime.fromtimestamp(ts_sec, BJT).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError, OverflowError):
                continue

            src_obj = it.get("source") if isinstance(it.get("source"), dict) else {}
            src_name = str(src_obj.get("display_zh") or src_obj.get("display") or "方程式新闻").strip()[:36]
            coins = [str(c.get("symbol"))[:16] for c in (it.get("coins") or []) if isinstance(c, dict) and c.get("symbol")][:4]
            path = str(it.get("path") or "")
            raw_url = str(it.get("url") or "").strip()
            if raw_url.startswith("https://api.eqn.news/"):
                raw_url = f"https://eqn.news{path}" if path.startswith("/") else "https://eqn.news/"
            elif not raw_url and path.startswith("/"):
                raw_url = f"https://eqn.news{path}"

            cat_code, cat_label = _classify_eqn_item(chs, raw_text, src_name)
            tone = "neutral"
            if raw_text.startswith(("🟢", "📈")) or "反弹" in raw_text or "上涨" in raw_text or "空头回补" in raw_text:
                tone = "bullish"
            elif raw_text.startswith(("🔻", "🔴")) or "下跌" in raw_text or "漏洞" in raw_text or "抽走" in raw_text or "警示" in raw_text:
                tone = "bearish"

            clean_head = re.sub(r"^[🟢🔴🔻🔸⚡📢🔥\s]+", "", raw_text).strip()
            feed_items.append({
                "id": 0,
                "ts": ts_sec,
                "dt_bjt": dt_str,
                "level": "major" if ("main" in chs or "major" in chs or "tfmoves" in chs) else "normal",
                "cat_code": cat_code,
                "category": cat_label,
                "source": src_name,
                "headline": clean_head[:180],
                "impact_label": f"涉及 {' / '.join(coins)}" if coins else "",
                "impact_tone": tone,
                "market_ref": "",
                "body": "",
                "coins": coins,
                "url": raw_url,
            })

            # Also extract actionable Earn & Alpha Airdrop items for the 理财羊毛 tab (within 6 days)
            if (cat_code == "EARN" or "earn" in chs) and (now_ts - ts_sec) <= 6 * 86400:
                if "请准备好领取币安 Alpha 空投" in clean_head or "返还款项" in clean_head or "全额返还" in clean_head:
                    continue
                exch = "Binance"
                low_all = f"{src_name} {clean_head}".lower()
                if "okx" in low_all:
                    exch = "OKX"
                elif "bybit" in low_all:
                    exch = "Bybit"
                elif "bitget" in low_all:
                    exch = "Bitget"
                elif "gate" in low_all:
                    exch = "Gate"
                elif "aster" in low_all:
                    exch = "Aster"

                tier = "HIGH" if re.search(r"(?:Simple\s*Earn|活期|Yield\s*Arena|Hold\s*&\s*Earn|Alpha\s*积分.*领取|瓜分)", clean_head, re.IGNORECASE) else "LOW_RISK"
                earn_cat, earn_cat_label = _classify_earn_promo(clean_head, "", tier)
                reward_m = re.search(r"(?:最高\s*[\d.]+%\s*(?:APR|APY|年化收益率?)|领取\s*[\d,]+\s*枚\s*[A-Z0-9]+|瓜分\s*[\d万亿.]+\s*[A-Z]+|[\d万亿.]+\s*[A-Z]+\s*奖励)", clean_head, re.IGNORECASE)
                p_title, p_summary = _split_promo_title_summary(clean_head, src_name)
                earn_promos.append({
                    "exchange": exch,
                    "tier": tier,
                    "tier_label": earn_cat_label,
                    "worth_level": earn_cat_label,
                    "earn_cat": earn_cat,
                    "earn_cat_label": earn_cat_label,
                    "promo_type": "理财提醒" if "earn" in chs else "Alpha空投",
                    "title": p_title,
                    "reward": reward_m.group(0) if reward_m else "",
                    "window": f"发布于 {dt_str[5:16]} BJT",
                    "summary": p_summary,
                    "url": raw_url,
                    "created_at": ts_sec,
                })

    return feed_items, earn_promos


def merge_eqn_into_bundle(
    bundle: dict[str, Any],
    eqn_feed: list[dict[str, Any]],
    eqn_promos: list[dict[str, Any]],
) -> None:
    """Merge EQN wires & earn promos with deduplication and balanced category quotas."""
    feed = bundle.get("feed")
    if isinstance(feed, dict) and eqn_feed:
        base_items = list(feed.get("items") or [])
        # Re-check upstream wire items that defaulted to CRYPTO/INTEL when actually macro TradFi wires
        for bi in base_items:
            if not isinstance(bi, dict):
                continue
            hl = str(bi.get("headline") or "")
            src_low = str(bi.get("source") or "").strip().lower()
            cc = str(bi.get("cat_code") or "CRYPTO")
            if cc == "CRYPTO" and not _CRYPTO_EXPLICIT_RE.search(hl):
                if src_low in _MACRO_SOURCES or _MACRO_KEYWORD_RE.search(hl):
                    bi["cat_code"] = "MACRO"
                    bi["category"] = "美股·宏观"
            elif cc == "INTEL" and src_low in _MACRO_SOURCES and not (_INTEL_SECURITY_RE.search(hl) or re.search(r"\bSEC\b", hl)):
                if not _CRYPTO_EXPLICIT_RE.search(hl):
                    bi["cat_code"] = "MACRO"
                    bi["category"] = "美股·宏观"
        seen_keys: set[str] = {_norm_title_key(it.get("headline", "")) for it in base_items if isinstance(it, dict)}
        for it in eqn_feed:
            k = _norm_title_key(it.get("headline", ""))
            if not k or k in seen_keys:
                continue
            seen_keys.add(k)
            base_items.append(it)
        base_items.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
        # Guarantee balanced representation across all 6 categories (up to 85 items total)
        cat_caps = {"CRYPTO": 22, "MACRO": 16, "LISTING": 16, "EARN": 12, "MOVES": 10, "INTEL": 12}
        cat_counts: dict[str, int] = {}
        balanced: list[dict[str, Any]] = []
        for it in base_items:
            cc = str(it.get("cat_code") or "CRYPTO")
            cap = cat_caps.get(cc, 15)
            if cat_counts.get(cc, 0) >= cap:
                continue
            cat_counts[cc] = cat_counts.get(cc, 0) + 1
            balanced.append(it)
            if len(balanced) >= 85:
                break
        feed["items"] = balanced

    labs = bundle.get("labs")
    if isinstance(labs, dict):
        promos = list(labs.get("exchange_promos") or [])
        for pr in promos:
            if isinstance(pr, dict) and not pr.get("earn_cat"):
                ec, el = _classify_earn_promo(str(pr.get("title") or ""), str(pr.get("summary") or ""), str(pr.get("tier") or ""))
                pr["earn_cat"] = ec
                pr["earn_cat_label"] = el
        seen_p = {_norm_title_key(p.get("title", "")) for p in promos if isinstance(p, dict)}
        seen_urls = {u for p in promos if isinstance(p, dict) for u in (_norm_promo_url(p.get("url", "")),) if u}
        seen_sem = {
            sk
            for p in promos
            if isinstance(p, dict)
            for sk in (_promo_semantic_key(str(p.get("exchange") or ""), str(p.get("title") or ""), str(p.get("summary") or ""), str(p.get("earn_cat") or "")),)
            if sk
        }
        for ep in eqn_promos:
            k = _norm_title_key(ep.get("title", ""))
            u = _norm_promo_url(ep.get("url", ""))
            sk = _promo_semantic_key(str(ep.get("exchange") or ""), str(ep.get("title") or ""), str(ep.get("summary") or ""), str(ep.get("earn_cat") or ""))
            if not k or k in seen_p or (u and u in seen_urls) or (sk and sk in seen_sem):
                continue
            seen_p.add(k)
            if u:
                seen_urls.add(u)
            if sk:
                seen_sem.add(sk)
            promos.append(ep)
        promos.sort(key=lambda p: (0 if p.get("earn_cat") in ("HIGH_YIELD", "AIRDROP") else 1 if p.get("earn_cat") == "CAMPAIGN" else 2, -int(p.get("created_at") or 0)))
        labs["exchange_promos"] = promos[:16]


DEFAULT_ZH_CACHE_PATH = Path.home() / ".cache" / "danclub" / "headline_zh_cache.json"
_ZH_CACHE_MAX_ENTRIES = 2500
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
_TRANS_BAD_RE = re.compile(r"(?:MYMEMORY\s+WARNING|QUERY\s+LENGTH\s+LIMIT|PLEASE\s+SELECT\s+TWO)", re.IGNORECASE)


def _needs_zh_translation(text: str) -> bool:
    """Return True when headline/body is predominantly English with little or no Chinese text."""
    s = str(text or "").strip()
    if len(s) < 12:
        return False
    cjk_cnt = len(_CJK_RE.findall(s))
    alpha_cnt = len(_ASCII_ALPHA_RE.findall(s))
    return (cjk_cnt < 4 and alpha_cnt >= 12) or (alpha_cnt >= 24 and cjk_cnt * 4 < alpha_cnt)


def _zh_cache_key(text: str) -> str:
    norm = re.sub(r"\s+", " ", str(text or "")).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _load_zh_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    try:
        if not cache_path.exists():
            return {}
        obj = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return {
                k: v
                for k, v in obj.items()
                if isinstance(k, str) and isinstance(v, dict) and bool(_valid_zh_result(str(v.get("zh") or "")))
            }
    except Exception:
        pass
    return {}


def _save_zh_cache(cache_path: Path, cache: dict[str, dict[str, Any]]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if len(cache) > _ZH_CACHE_MAX_ENTRIES:
            ordered = sorted(cache.items(), key=lambda kv: int(kv[1].get("ts") or 0), reverse=True)
            cache = dict(ordered[:_ZH_CACHE_MAX_ENTRIES])
        payload = json.dumps(cache, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(prefix=".zhcache.", dir=str(cache_path.parent))
        try:
            os.chmod(tmp_name, 0o600)
            view = memoryview(payload)
            written = 0
            while written < len(view):
                n = os.write(fd, view[written:])
                if n <= 0:
                    raise OSError("short write")
                written += n
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(tmp_name, str(cache_path))
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
    except Exception:
        pass


def _valid_zh_result(zh: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(zh or "")).strip()
    cleaned = re.sub(r"\s*([：，。！？；])\s*", r"\1", cleaned)
    if not cleaned or len(cleaned) > 240:
        return ""
    if _TRANS_BAD_RE.search(cleaned):
        return ""
    if len(_CJK_RE.findall(cleaned)) < 2:
        return ""
    return cleaned


def _translate_batch_clients5(texts: list[str], timeout: float = 6.0) -> list[str]:
    """Batch-translate short English headlines via Google clients5 repeated-q API (independent JSON items)."""
    clean_in = [re.sub(r"[\r\n]+", " ", t).strip() for t in texts]
    if not clean_in:
        return []
    qs = "&".join(
        ["client=dict-chrome-ex", "sl=en", "tl=zh-CN"]
        + ["q=" + url_quote(t) for t in clean_in]
    )
    url = "https://clients5.google.com/translate_a/t?" + qs
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=max(0.2, float(timeout))) as resp:
        data = json.loads(resp.read(256 * 1024).decode("utf-8", errors="replace"))
    if not isinstance(data, list) or len(data) != len(clean_in):
        return []
    out: list[str] = []
    for row in data:
        raw_item = row[0] if isinstance(row, list) and row else row
        if not isinstance(raw_item, str):
            return []
        out.append(_valid_zh_result(re.sub(r"[\r\n]+", " ", raw_item)))
    return out


def _translate_single_mymemory(text: str, timeout: float = 5.0) -> str:
    clean_in = re.sub(r"[\r\n]+", " ", text).strip()
    if not clean_in or len(clean_in) > 260:
        # Never silently truncate & cache under the full-text hash
        return ""
    url = "https://api.mymemory.translated.net/get?langpair=en|zh-CN&q=" + url_quote(clean_in)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=max(0.2, float(timeout))) as resp:
        data = json.loads(resp.read(128 * 1024).decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        return ""
    tr = (data.get("responseData") or {}).get("translatedText")
    return _valid_zh_result(str(tr or ""))


def _default_translate_en_list(texts: list[str], *, max_wall_sec: float = 18.0) -> list[str]:
    """Translate up to N English strings in small batches with wall-clock deadline & circuit breaker."""
    results = [""] * len(texts)
    deadline = time.monotonic() + max_wall_sec
    consec_fail = 0
    idx = 0
    while idx < len(texts) and time.monotonic() < deadline and consec_fail < 2:
        batch_indices: list[int] = []
        batch_texts: list[str] = []
        char_budget = 0
        while idx < len(texts) and len(batch_texts) < 6:
            t = texts[idx]
            if batch_texts and (char_budget + len(t) > 700):
                break
            batch_indices.append(idx)
            batch_texts.append(t)
            char_budget += len(t) + 4
            idx += 1

        rem = deadline - time.monotonic()
        if rem < 1.0:
            break
        translated: list[str] = []
        try:
            translated = _translate_batch_clients5(batch_texts, timeout=min(6.0, rem))
        except Exception:
            translated = []

        if len(translated) == len(batch_texts) and any(translated):
            consec_fail = 0
            for bi, zh in zip(batch_indices, translated):
                results[bi] = zh
        else:
            # Fallback individually if batch endpoint failed
            batch_ok = 0
            for bi, src_txt in zip(batch_indices, batch_texts):
                rem = deadline - time.monotonic()
                if rem < 1.0:
                    break
                zh = ""
                try:
                    single = _translate_batch_clients5([src_txt], timeout=min(5.0, rem))
                    zh = single[0] if single else ""
                except Exception:
                    pass
                rem = deadline - time.monotonic()
                if not zh and rem >= 1.0:
                    try:
                        zh = _translate_single_mymemory(src_txt, timeout=min(5.0, rem))
                    except Exception:
                        zh = ""
                if zh:
                    results[bi] = zh
                    batch_ok += 1
            if batch_ok > 0:
                consec_fail = 0
            else:
                consec_fail += 1
    return results


def _run_translate_en_list_bounded(texts: list[str], *, hard_timeout_sec: float = 22.0) -> list[str]:
    """Run external translation in a killable subprocess so trickle reads can NEVER stall snapshot publish."""
    if not texts:
        return []
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--translate-worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    payload = json.dumps(texts, ensure_ascii=False).encode("utf-8")
    try:
        out_b, _ = proc.communicate(input=payload, timeout=hard_timeout_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return []
    if proc.returncode != 0 or not out_b:
        return []
    try:
        parsed = json.loads(out_b.decode("utf-8", errors="replace"))
        if isinstance(parsed, list) and len(parsed) == len(texts):
            return [str(x or "") for x in parsed]
    except Exception:
        pass
    return []


def translate_english_feed_items(
    bundle: dict[str, Any],
    *,
    cache_path: Path = DEFAULT_ZH_CACHE_PATH,
    translator: Any = None,
    max_uncached: int = 45,
    blocklist_path: Path | None = None,
    require_blocklist: bool = False,
) -> int:
    """Translate English-only feed headlines/bodies & promo titles to Simplified Chinese with persistent caching."""
    cache = _load_zh_cache(cache_path)
    now_ts = int(time.time())
    targets: list[tuple[dict[str, Any], str, str | None, str]] = []  # (obj, field, en_backup_field, src_text)

    feed = bundle.get("feed")
    if isinstance(feed, dict):
        for it in feed.get("items") or []:
            if not isinstance(it, dict):
                continue
            hl = str(it.get("headline") or "").strip()
            if _needs_zh_translation(hl):
                targets.append((it, "headline", "headline_en", hl))
            bd = str(it.get("body") or "").strip()
            if _needs_zh_translation(bd):
                targets.append((it, "body", None, bd))

    labs = bundle.get("labs")
    if isinstance(labs, dict):
        for pr in labs.get("exchange_promos") or []:
            if not isinstance(pr, dict):
                continue
            tt = str(pr.get("title") or "").strip()
            if _needs_zh_translation(tt):
                targets.append((pr, "title", None, tt))

    if not targets:
        return 0

    uncached_map: dict[str, str] = {}
    for _, _, _, txt in targets:
        if len(txt) > 240:
            continue
        ck = _zh_cache_key(txt)
        if ck not in cache and ck not in uncached_map and len(uncached_map) < max_uncached:
            # Fail-closed pre-egress privacy gate: NEVER transmit sensitive/blocklisted text to third-party APIs
            clean_egress = sanitize_text(txt, max_len=240)
            if not clean_egress:
                continue
            try:
                assert_clean_payload(
                    clean_egress,
                    blocklist_path=blocklist_path,
                    require_blocklist=require_blocklist,
                )
            except PrivacyViolationError:
                continue
            uncached_map[ck] = clean_egress

    cache_dirty = False
    if uncached_map:
        keys_list = list(uncached_map.keys())
        texts_list = [uncached_map[k] for k in keys_list]
        fn = translator or _run_translate_en_list_bounded
        try:
            zh_list = fn(texts_list)
        except Exception:
            zh_list = []
        if isinstance(zh_list, list) and len(zh_list) == len(keys_list):
            for ck, zh_raw in zip(keys_list, zh_list):
                zh_ok = _valid_zh_result(zh_raw)
                if zh_ok:
                    cache[ck] = {"zh": zh_ok, "ts": now_ts}
                    cache_dirty = True

    applied = 0
    for obj, field, en_field, src_txt in targets:
        ck = _zh_cache_key(src_txt)
        hit = cache.get(ck)
        if hit and hit.get("zh"):
            if en_field and not obj.get(en_field):
                obj[en_field] = src_txt
            obj[field] = hit["zh"]
            hit["ts"] = now_ts
            applied += 1

    if cache_dirty:
        _save_zh_cache(cache_path, cache)
    return applied


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

    # 3. Feed text sanitization & URL allowlisting (relaxed 85-item multi-source wire)
    feed = bundle["feed"]
    if isinstance(feed.get("daily_analysis"), dict):
        feed["daily_analysis"]["headline"] = sanitize_text(feed["daily_analysis"].get("headline"), max_len=140)
        feed["daily_analysis"]["body"] = sanitize_text(feed["daily_analysis"].get("body"), max_len=420)
    clean_items = []
    for it in feed.get("items", []):
        if not isinstance(it, dict):
            continue
        it["headline"] = sanitize_text(it.get("headline"), max_len=190)
        if it.get("headline_en"):
            clean_en = sanitize_text(it.get("headline_en"), max_len=190)
            if clean_en:
                it["headline_en"] = clean_en
            else:
                it.pop("headline_en", None)
        it["body"] = sanitize_text(it.get("body"), max_len=240)
        it["source"] = sanitize_text(it.get("source"), max_len=40)
        it["impact_label"] = sanitize_text(it.get("impact_label"), max_len=80)
        it["market_ref"] = sanitize_text(it.get("market_ref"), max_len=120)
        it["url"] = sanitize_public_url(it.get("url"), allowed_domains=ALLOWED_NEWS_DOMAINS)
        if not it.get("cat_code"):
            it["cat_code"] = "CRYPTO"
        if it["headline"]:
            clean_items.append(it)
    feed["items"] = clean_items[:85]

    # 4. Promos URL allowlist + text sanitization (fused exchange_promos + EQN earn/alpha)
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
        if not pr.get("earn_cat"):
            ec, el = _classify_earn_promo(pr["title"], pr["summary"], str(pr.get("tier") or ""))
            pr["earn_cat"] = ec
            pr["earn_cat_label"] = el
        if pr["title"]:
            clean_promos.append(pr)
    labs["exchange_promos"] = clean_promos[:18]

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
        fl["x_url"] = sanitize_public_url(fl.get("x_url"), allowed_domains=("x.com",))
        if fl.get("x_handle") and not re.match(r"^[A-Za-z0-9_]{2,15}$", str(fl["x_handle"])):
            fl["x_handle"] = None
            fl["x_url"] = ""
        _require_bounded_int(fl.get("addr_count", 1), f"whales.flagships[{i}].addr_count", 1, 1000)

    # Strict allowlist reconstruction for derived recent_moves, liq_magnets & CoinGlass liquidations
    valid_flagship_ids = {str(fl.get("id") or "") for fl in whales.get("flagships", []) if isinstance(fl, dict) and fl.get("id")}
    clean_moves = []
    for mv in whales.get("recent_moves") or []:
        if not isinstance(mv, dict):
            continue
        mid = sanitize_text(mv.get("id"), max_len=24)
        disp = sanitize_text(mv.get("display"), max_len=40)
        if not disp or mid not in valid_flagship_ids:
            continue
        clean_moves.append({
            "id": mid,
            "display": disp,
            "tier_code": sanitize_text(mv.get("tier_code"), max_len=4),
            "tier_label": sanitize_text(mv.get("tier_label"), max_len=24),
            "coin": sanitize_text(mv.get("coin"), max_len=20),
            "side": "LONG" if str(mv.get("side") or "").upper() == "LONG" else "SHORT",
            "action": sanitize_text(mv.get("action"), max_len=12),
            "delta_usd": mv.get("delta_usd"),
            "delta_window_h": mv.get("delta_window_h"),
            "net_usd": mv.get("net_usd"),
            "ts": int(mv.get("ts") or 0),
        })
    whales["recent_moves"] = clean_moves[:15]

    clean_mags: dict[str, Any] = {}
    if isinstance(whales.get("liq_magnets"), dict):
        for m_coin, mag in whales["liq_magnets"].items():
            if not isinstance(mag, dict):
                continue
            mc = sanitize_text(str(m_coin), max_len=16)
            if not mc:
                continue
            m_out: dict[str, Any] = {
                "mark_px": mag.get("mark_px"),
                "source": sanitize_text(mag.get("source"), max_len=48),
            }
            for wall_k in ("long_wall", "short_wall"):
                wall = mag.get(wall_k)
                if not isinstance(wall, dict):
                    continue
                t_list = []
                for tgt in wall.get("targets") or []:
                    if not isinstance(tgt, dict):
                        continue
                    tid = sanitize_text(tgt.get("id"), max_len=24)
                    tdisp = sanitize_text(tgt.get("display"), max_len=40)
                    if not tdisp or tid not in valid_flagship_ids:
                        continue
                    t_list.append({
                        "id": tid,
                        "display": tdisp,
                        "tier_code": sanitize_text(tgt.get("tier_code"), max_len=4),
                        "liq_px": tgt.get("liq_px"),
                        "dist_pct": tgt.get("dist_pct"),
                        "usd": tgt.get("usd"),
                        "lev": tgt.get("lev"),
                        "venue": sanitize_text(tgt.get("venue"), max_len=16),
                    })
                m_out[wall_k] = {
                    "nearest_px": wall.get("nearest_px"),
                    "nearest_dist_pct": wall.get("nearest_dist_pct"),
                    "within_10pct_usd": wall.get("within_10pct_usd", 0.0),
                    "within_10pct_count": int(wall.get("within_10pct_count") or 0),
                    "within_25pct_usd": wall.get("within_25pct_usd", 0.0),
                    "within_25pct_count": int(wall.get("within_25pct_count") or 0),
                    "targets": t_list[:6],
                }
            clean_mags[mc] = m_out
    whales["liq_magnets"] = clean_mags

    liq = whales.get("liquidations")
    if liq is None and isinstance(bundle.get("liquidations"), dict):
        liq = bundle["liquidations"]
    if isinstance(liq, dict):
        _require_bounded_int(liq.get("exchanges_count", 0), "whales.liquidations.exchanges_count", 0, 50)
        clean_liq_evs = []
        for ev in liq.get("recent_events") or []:
            if not isinstance(ev, dict):
                continue
            ec = sanitize_text(ev.get("coin"), max_len=16)
            if not ec:
                continue
            clean_liq_evs.append({
                "ts": int(ev.get("ts") or 0),
                "ex": sanitize_text(ev.get("ex"), max_len=24),
                "ex_short": sanitize_text(ev.get("ex_short"), max_len=12),
                "coin": ec,
                "side": "LONG" if str(ev.get("side") or "").upper() == "LONG" else "SHORT",
                "usd": ev.get("usd"),
                "px": ev.get("px"),
            })
        clean_wicks = []
        for wk in liq.get("wick_alerts") or []:
            if not isinstance(wk, dict):
                continue
            wt = sanitize_text(wk.get("title"), max_len=84)
            if not wt:
                continue
            clean_wicks.append({
                "ts": int(wk.get("ts") or 0),
                "coin": sanitize_text(wk.get("coin"), max_len=16),
                "kind": sanitize_text(wk.get("kind"), max_len=16),
                "level": "major" if str(wk.get("level") or "").lower() == "major" else "normal",
                "title": wt,
                "detail": sanitize_text(wk.get("detail"), max_len=180),
            })
        clean_wins: dict[str, dict[str, Any]] = {}
        if isinstance(liq.get("windows"), dict):
            for wn in ("1h", "24h"):
                wmap = liq["windows"].get(wn)
                if not isinstance(wmap, dict):
                    continue
                clean_wins[wn] = {}
                for sc in ("ALL", "BTC", "ETH", "SOL", "HYPE"):
                    b = wmap.get(sc)
                    if not isinstance(b, dict):
                        continue
                    top_ex_clean = []
                    for tx in (b.get("top_exchanges") or [])[:4]:
                        if isinstance(tx, dict):
                            top_ex_clean.append({
                                "ex": sanitize_text(tx.get("ex"), max_len=12),
                                "usd": tx.get("usd"),
                                "pct": tx.get("pct"),
                            })
                    clean_wins[wn][sc] = {
                        "long_usd": b.get("long_usd", 0.0),
                        "short_usd": b.get("short_usd", 0.0),
                        "total_usd": b.get("total_usd", 0.0),
                        "count": _require_bounded_int(int(b.get("count") or 0), f"whales.liquidations.windows.{wn}.{sc}.count", 0, 10_000_000),
                        "top_exchanges": top_ex_clean,
                    }
        whales["liquidations"] = {
            "available": bool(liq.get("available")),
            "source": sanitize_text(liq.get("source"), max_len=48) or "CoinGlass 全网永续强平统计",
            "updated_at": int(liq.get("updated_at") or 0),
            "exchanges_count": int(liq.get("exchanges_count") or 0),
            "windows": clean_wins,
            "recent_events": clean_liq_evs[:40],
            "wick_alerts": clean_wicks[:10],
        }
    else:
        whales["liquidations"] = {
            "available": False,
            "source": "CoinGlass 全网永续强平统计",
            "updated_at": 0,
            "exchanges_count": 0,
            "windows": {},
            "recent_events": [],
            "wick_alerts": [],
        }

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
    static_rel_list = " ".join(shlex.quote(rel) for rel in WHITELISTED_STATIC_FILES)
    non_index_rel_list = " ".join(shlex.quote(rel) for rel in WHITELISTED_STATIC_FILES if rel != "index.html")
    remote_cmd = (
        f"set -eu; "
        f"test ! -L {q_parent}; "
        f"if [ -e {q_dir} ]; then test -d {q_dir} && test ! -L {q_dir}; else mkdir -p {q_dir}; fi; "
        f"for sub in assets assets/audio api api/v1; do "
        f"  if [ -e {q_dir}/\"$sub\" ]; then test -d {q_dir}/\"$sub\" && test ! -L {q_dir}/\"$sub\"; else mkdir -p {q_dir}/\"$sub\"; fi; "
        f"done; "
        f"tmp=$(mktemp -d {q_dir}/.stage.XXXXXX); "
        f"trap 'rm -rf \"$tmp\"' EXIT; "
        f"tar -xzf - --no-same-owner --no-same-permissions -C \"$tmp\"; "
        f"(cd \"$tmp\" && sha256sum -c .manifest.sha256 >/dev/null); "
        f"find \"$tmp\" -type f -exec chmod 0644 {{}} +; "
        f"for rel in {static_rel_list}; do test ! -L {q_dir}/\"$rel\" && test ! -d {q_dir}/\"$rel\"; done; "
        f"for f in \"$tmp/api/v1/\"*.json; do bn=$(basename \"$f\"); test ! -L {q_dir}/api/v1/\"$bn\" && test ! -d {q_dir}/api/v1/\"$bn\"; done; "
        f"for rel in {non_index_rel_list}; do mv -fT -- \"$tmp/$rel\" {q_dir}/\"$rel\"; done; "
        f"for f in \"$tmp/api/v1/\"*.json; do mv -fT -- \"$f\" {q_dir}/api/v1/\"$(basename \"$f\")\"; done; "
        f"mv -fT -- \"$tmp/index.html\" {q_dir}/index.html"
    )
    proc = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, remote_cmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _, err_b = proc.communicate(input=tar_bytes, timeout=60)
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
    parser.add_argument("--skip-external", action="store_true", help="Skip external EQN news & Binance bStocks live HTTP fetches")
    parser.add_argument("--translate-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repo-url", default="git@github.com:danclub-open/danclub-terminal.git", help="Git remote URL")
    args = parser.parse_args()

    if args.translate_worker:
        try:
            raw_in = sys.stdin.read(512 * 1024)
            texts = json.loads(raw_in) if raw_in else []
            if not isinstance(texts, list):
                texts = []
            out = _default_translate_en_list([str(t or "") for t in texts], max_wall_sec=18.0)
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
            return 0
        except Exception:
            return 1

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
        if not args.skip_external and not args.input:
            bstocks = fetch_binance_bstocks()
            apply_bstocks_overlay(raw, bstocks)
            eqn_feed, eqn_promos = fetch_eqn_news_and_earn()
            merge_eqn_into_bundle(raw, eqn_feed, eqn_promos)
            translate_english_feed_items(
                raw,
                blocklist_path=args.blocklist,
                require_blocklist=args.require_blocklist,
            )
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
