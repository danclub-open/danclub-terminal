#!/usr/bin/env python3
"""Read-only whitelisted public market data exporter for DanClub Open Terminal.

Security guarantees:
- Opens ONLY explicitly whitelisted public market SQLite databases in strict read-only URI mode.
- NEVER accesses private chat logs, personal holdings, credentials, or audit logs.
- Enforces the shared privacy_guard fail-closed check before writing any --out-dir files.
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

# Allow importing shared privacy_guard both in monorepo layout and standalone deployment
_HERE = Path(__file__).resolve().parent
for _cand in (_HERE, _HERE.parent / "scripts"):
    if (_cand / "privacy_guard.py").is_file() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

try:
    import privacy_guard as _pg
except ImportError:
    _pg = None

BJT = timezone(timedelta(hours=8))
DEFAULT_DATA_DIR = Path(os.environ.get("DANCLUB_DATA_DIR", str(Path(__file__).resolve().parent / "data")))

BOTTOM_KEYS: list[tuple[str, str]] = [
    ("btc_low", "BTC 守住前低"),
    ("etf_flow", "现货 ETF 流向"),
    ("oi_funding", "合约杠杆与费率"),
    ("stables", "稳定币供给"),
    ("us10y", "美债 10Y 收益率"),
    ("cb_premium", "Coinbase 溢价"),
    ("crypto_stocks", "币股联动情绪"),
]

PUBLIC_FEED_CATEGORIES = ("秒级快讯", "交易所公告", "情报预警", "每日短线研判")

MAJOR_WALLET_COINS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
    "TRX", "TON", "SUI", "UNI", "LTC", "BCH", "NEAR", "APT", "ARB", "OP",
    "ATOM", "FIL", "AAVE", "HYPE", "USDT", "USDC", "FDUSD", "USDE", "DAI",
    "PEPE", "SHIB", "WIF", "TAO", "RENDER", "FET", "ENA", "ONDO", "MNT",
}

MACRO_WATCHLIST: list[tuple[str, str, str]] = [
    ("SPY", "标普500 ETF", "美股指数"),
    ("QQQ", "纳指100 ETF", "美股指数"),
    (".VIX", "恐慌指数 VIX", "波动率"),
    ("US10Y", "美债 10Y", "利率"),
    (".DXY", "美元指数", "外汇"),
    ("JPY=", "美元/日元", "外汇"),
    ("CNH=", "离岸人民币", "外汇"),
    ("XAU=", "现货黄金", "贵金属"),
    ("XAG=", "现货白银", "贵金属"),
    ("NVDA", "英伟达", "算力存储"),
    ("MU", "美光科技", "算力存储"),
    ("SNDK", "闪迪 SanDisk", "算力存储"),
    ("MSTR", "Strategy", "币股"),
    ("COIN", "Coinbase", "币股"),
    ("CRCL", "Circle", "币股"),
    ("HOOD", "Robinhood", "币股"),
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


def export_overview(data_dir: Path) -> dict[str, Any]:
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
            bottom_items.append({
                "key": key,
                "label": label,
                "status": status,
                "detail": str(row.get("detail") or "")[:240],
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
                "total_b": _num(stables.get("total_b"), 2),
                "chg_1d_b": _num(stables.get("chg_1d_b"), 2),
                "chg_7d_b": _num(stables.get("chg_7d_b"), 2),
                "chg_30d_b": _num(stables.get("chg_30d_b"), 2),
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
        if isinstance(qdii, dict) and isinstance(qdii.get("items"), list):
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

        macro_quotes = []
        for sym, name, group in MACRO_WATCHLIST:
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
            prev, _ = _safe_query(
                conn,
                "SELECT price FROM quotes_60m WHERE symbol = ? AND ts_sec <= ? ORDER BY ts_sec DESC LIMIT 1",
                (sym, ts_now - 86400),
            )
            if not prev:
                prev, _ = _safe_query(
                    conn,
                    "SELECT price FROM quotes_60m WHERE symbol = ? ORDER BY ts_sec ASC LIMIT 1",
                    (sym,),
                )
            chg_pct = None
            if prev:
                p0 = _num(prev[0].get("price"), 8)
                if p0 is not None and p0 > 0:
                    chg_pct = _num((px_now / p0 - 1.0) * 100.0, 2)
            macro_quotes.append({
                "symbol": sym,
                "name": name,
                "group": group,
                "price": px_now,
                "chg_pct": chg_pct,
                "ts": ts_now,
            })

        # Latest 14 trading days (DESC LIMIT 14, then reversed to chronological ASC)
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
            "btc_miner": miner_obj,
            "qdii_premium": qdii_items,
            "macro_quotes": macro_quotes,
            "us10y_history": us10y_hist,
        }
    finally:
        if conn:
            conn.close()


def export_arb(data_dir: Path) -> dict[str, Any]:
    now_sec = int(time.time())
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
            # Latest 14 days (DESC LIMIT 14 then reversed to ASC)
            daily_desc, _ = _safe_query(
                conn_sp,
                "SELECT date_bjt, samples, last_px_a, last_px_b_raw, last_px_b_adj, last_spread_pct, min_spread_pct, max_spread_pct, sum_spread_pct FROM spread_daily WHERE pair_id = ? ORDER BY date_bjt DESC LIMIT 14",
                (pid,),
            )
            daily = list(reversed(daily_desc))

            raw_24h, _ = _safe_query(
                conn_sp,
                "SELECT ts, px_a, px_b_adj, spread_pct FROM spread_ticks WHERE pair_id = ? AND ts >= ? ORDER BY ts ASC",
                (pid, now_sec - 86400),
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
                (pid, now_sec - 72 * 3600),
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

    # CSI Index Futures Basis
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

    # Four-Exchange Wallet Deposit/Withdraw Status (fail-closed availability metadata)
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
                1 for w in wallet_summary if w["updated_at"] > 0 and (now_sec - w["updated_at"]) <= WALLET_STALE_SEC
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


def export_flows(data_dir: Path) -> dict[str, Any]:
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
            # Fix window first (last 5 / 10 / all trading days), then sum valid entries without shifting window
            def _sum_window(slice_items: list[dict[str, Any]]) -> float | None:
                nums = [x["total_musd"] for x in slice_items if x["total_musd"] is not None]
                return _num(sum(nums), 1) if nums else None

            etf_data["summary"][asset] = {
                "days": len(items),
                "sum_5d_musd": _sum_window(items[-5:]),
                "sum_10d_musd": _sum_window(items[-10:]),
                "sum_all_musd": _sum_window(items),
                "latest_date": items[-1]["date"] if items else None,
                "latest_musd": items[-1]["total_musd"] if items else None,
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
        now_sec = int(time.time())
        hist, _ = _safe_query(
            conn_mstr,
            "SELECT ts, mnav, btc_price FROM kpi_snapshots WHERE ts >= ? ORDER BY ts ASC",
            (now_sec - 7 * 86400,),
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
            (int(time.time()) - 86400,),
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

    oi_radar = []
    conn_opp = _connect_ro(data_dir, "opportunity.db")
    try:
        rows, _ = _safe_query(
            conn_opp,
            """
            SELECT symbol, base, price, oi_usd, vol_24h, chg_1h_oi, chg_1h_px, funding_rate, updated_at
            FROM bn_oi_latest
            WHERE chg_1h_oi IS NOT NULL
            ORDER BY chg_1h_oi DESC
            LIMIT 20
            """,
        )
        for r in rows:
            oi1h = _num(r.get("chg_1h_oi"), 2)
            px1h = _num(r.get("chg_1h_px"), 2)
            fr = _num(r.get("funding_rate"), 8)
            if oi1h is None:
                continue
            if oi1h >= 2.0 and (px1h is not None and px1h >= 0.5):
                regime, tone = "追多拥挤·忌追高", "warn"
            elif oi1h >= 2.0 and (px1h is not None and px1h <= -0.5):
                regime, tone = "空头扎堆·防逼空", "squeeze"
            elif oi1h >= 2.0:
                regime, tone = "杠杆建仓·观望方向", "neutral"
            else:
                regime, tone = "常态波动", "muted"
            oi_radar.append({
                "symbol": str(r.get("symbol") or ""),
                "base": str(r.get("base") or ""),
                "price": _num(r.get("price"), 6),
                "oi_usd": _num(r.get("oi_usd"), 0),
                "vol_24h": _num(r.get("vol_24h"), 0),
                "chg_1h_oi_pct": oi1h,
                "chg_1h_px_pct": px1h,
                "funding_8h_pct": _num(fr * 100.0, 4) if fr is not None else None,
                "regime": regime,
                "tone": tone,
                "updated_at": _int_or_zero(r.get("updated_at")),
            })
    finally:
        if conn_opp:
            conn_opp.close()

    return {
        "etf_flows": etf_data,
        "mstr": mstr_obj,
        "hyperliquid": hl_obj,
        "bn_oi_radar": oi_radar,
    }


def export_labs(data_dir: Path) -> dict[str, Any]:
    dca_runs = []
    latest_summary = None
    conn_dca = _connect_ro(data_dir, "dca_sim.db")
    try:
        # Latest 90 days (DESC LIMIT 90, then reversed to chronological ASC so day 91+ is ALWAYS latest!)
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
            for strat in ("FIXED", "VA", "MA200_FNG"):
                s = sj.get(strat)
                if isinstance(s, dict):
                    has_strat = True
                    point[strat] = {
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
        strat_names = {
            "FIXED": "固定定投（每日每标的 100U）",
            "VA": "价值平均法（跌多买、涨少买）",
            "MA200_FNG": "200日均线 + 恐慌指数加权",
        }
        for key, label in strat_names.items():
            s = latest_summary.get(key)
            if not isinstance(s, dict):
                continue
            per = {}
            if isinstance(s.get("per"), dict):
                for asset, ret in s["per"].items():
                    ak = str(asset).strip().upper()
                    if ASSET_KEY_RE.match(ak):
                        per[ak] = _num(ret, 2)
            latest_strategies[key] = {
                "label": label,
                "in": _num(s.get("in"), 2),
                "mv": _num(s.get("mv"), 2),
                "pnl": _num(s.get("pnl"), 2),
                "ret_pct": _num(s.get("ret"), 2),
                "days": max(0, _int_or_zero(s.get("days"))),
                "per_asset_ret_pct": per,
            }

    promos = []
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
            LIMIT 16
            """,
        )
        for r in rows:
            title = str(r.get("title") or "").strip()
            summary = str(r.get("summary_text") or "").strip()
            if not title or GENERIC_PRIVATE_RE.search(title) or GENERIC_PRIVATE_RE.search(summary):
                continue
            promos.append({
                "exchange": str(r.get("exchange") or "")[:32],
                "promo_type": str(r.get("promo_type") or "")[:32],
                "worth_level": str(r.get("worth_level") or "")[:40],
                "title": title[:140],
                "summary": summary[:260],
                "url": _clean_promo_url(r.get("url")),
                "created_at": _int_or_zero(r.get("created_at")),
            })
    finally:
        if conn_pr:
            conn_pr.close()

    return {
        "dca_simulation": {
            "disclaimer": "纯虚拟模拟盘（无实盘资金、0.1%模拟手续费），仅作公开量化方法论长期透明对照。",
            "latest_date": dca_runs[-1]["date"] if dca_runs else None,
            "strategies": latest_strategies,
            "history": dca_runs,
        },
        "exchange_promos": promos,
    }


def export_feed(data_dir: Path) -> dict[str, Any]:
    items = []
    short_analysis = None
    conn_arc = _connect_ro(data_dir, "archive_14d.db")
    try:
        placeholders = ",".join("?" for _ in PUBLIC_FEED_CATEGORIES)
        rows, _ = _safe_query(
            conn_arc,
            f"""
            SELECT id, ts, dt_bjt, level, category, msg
            FROM message_archive
            WHERE category IN ({placeholders})
            ORDER BY id DESC
            LIMIT 80
            """,
            PUBLIC_FEED_CATEGORIES,
        )
        for r in rows:
            msg = str(r.get("msg") or "").strip()
            if not msg or GENERIC_PRIVATE_RE.search(msg):
                continue
            if "X快讯·@" in msg or "推圈" in msg:
                continue
            cat = str(r.get("category") or "")
            lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
            headline = lines[0][:140] if lines else ""
            body = "\n".join(lines[1:])[:360] if len(lines) > 1 else ""
            entry = {
                "id": _int_or_zero(r.get("id")),
                "ts": _int_or_zero(r.get("ts")),
                "dt_bjt": str(r.get("dt_bjt") or ""),
                "level": str(r.get("level") or "normal"),
                "category": cat,
                "headline": headline,
                "body": body,
            }
            if cat == "每日短线研判" and short_analysis is None:
                short_analysis = entry
            elif len(items) < 35:
                items.append(entry)
    finally:
        if conn_arc:
            conn_arc.close()

    return {
        "daily_analysis": short_analysis,
        "items": items,
    }


def build_bundle(data_dir: Path) -> dict[str, Any]:
    now_dt = datetime.now(BJT)
    meta = {
        "project": "DanClub Open Terminal",
        "license": "MIT",
        "generated_at_bjt": now_dt.strftime("%Y-%m-%d %H:%M:%S +08:00"),
        "generated_ts": int(now_dt.timestamp()),
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
    return {
        "meta": meta,
        "overview": export_overview(data_dir),
        "arb": export_arb(data_dir),
        "flows": export_flows(data_dir),
        "labs": export_labs(data_dir),
        "feed": export_feed(data_dir),
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
        # Enforce the SAME fail-closed privacy validator before writing ANY standalone public file
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
