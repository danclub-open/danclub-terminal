#!/usr/bin/env python3
"""Read-only privacy-safe public whale & faction exporter for DanClub Terminal.

Security & Accuracy Guarantees:
- Opens whale_intel.db in strict SQLite read-only URI mode (mode=ro, query_only=ON).
- NEVER reads or exports raw_tweets, crawl_state, referral codes, internal labels, or personal data.
- Public names come ONLY from official Hyperliquid display names (hl_display_name); otherwise masked 0x address.
- Fail-closed: refuses to output anything if privacy_guard is unavailable or detects any violation.
- Constant-price quantity delta (SUM((cur_szi - prev_szi) * cur_mark_px)) prevents mark-to-market price moves
  from generating false add/reduce position actions.
- Full-position accounting matches current vs historical net exposure with zero threshold mismatch.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    import privacy_guard as _pg
except ImportError:
    _pg = None

BJT = timezone(timedelta(hours=8))

TIER_META: dict[str, tuple[str, str, int]] = {
    "S_SMART_WHALE": ("S", "S级聪明钱", 1),
    "A_PROFITABLE_WHALE": ("A", "A级盈利鲸", 2),
    "C_COUNTER_WHALE": ("C", "C级反指巨鲸", 3),
    "F_FORMER_LEGEND": ("F", "F级昔日传奇", 4),
}

# Only allow clean official Hyperliquid or verified public leaderboard display names (no URLs/emails/sentences)
_BAD_PUBLIC_NAME_RE = re.compile(
    r"(?:http|@|\.com|\.org|\.io|microstrategy|blackrock|binance|coinbase|okx|upbit|bybit|tether|circle|bitget|gate|青桐|铁头|胜率|巨鲸|实体|大户)",
    re.IGNORECASE,
)
_X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,15}$")
_EXCLUDED_X_HANDLES: frozenset[str] = frozenset({
    "bitget", "bitget_zh", "bitgetglobal", "gate_zh", "gate_io", "gate",
    "binance", "binancezh", "binancewallet", "binance_intern",
    "okx", "okx_ventures", "okxchinese", "bybit_official", "hyperliquidx",
    "coinbase", "krakenfx", "upbitglobal", "bithumbofficial",
    "ai_9684xtpa", "embercn", "lookonchain", "onchainlens", "arkham",
    "nansen_ai", "spotonchain", "whale_alert", "bwenews", "eqnnews",
})


def _extract_verified_x_and_alias(
    hl_name: str | None,
    ext_labels: list[str],
    obs_labels: list[str],
    short_addr: str,
) -> tuple[str, str | None, str]:
    """Return (public_display_name, verified_x_handle_or_None, x_url).
    STRICT: NEVER reads private address_registry.entity_label; ONLY verified official HL display names
    and whitelisted public leaderboard observations (hl_leaderboard / wsm_hyperdash_seed).
    """
    x_handle: str | None = None
    for src_list in (ext_labels, obs_labels):
        if x_handle:
            break
        for raw in src_list:
            s = str(raw or "").strip()
            for m in re.finditer(r"@([A-Za-z0-9_]{2,15})", s):
                cand = m.group(1)
                if _X_HANDLE_RE.match(cand) and cand.lower() not in _EXCLUDED_X_HANDLES:
                    x_handle = cand
                    break
            if x_handle:
                break

    def _clean_alias(v: str | None) -> str:
        s = re.sub(r"\s*\(@[A-Za-z0-9_]{2,15}\)\s*", "", str(v or ""))
        s = re.sub(r"^X:@", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s).strip()
        if (
            s
            and 2 <= len(s) <= 22
            and not any(ch in s for ch in ("，", "。", "！", "？", "\n", ":", "/"))
            and not _BAD_PUBLIC_NAME_RE.search(s)
            and all(ord(ch) < 128 for ch in s)
        ):
            return s
        return ""

    display = _clean_alias(hl_name)
    if not display:
        for raw in (*ext_labels, *obs_labels):
            c = _clean_alias(raw)
            if c:
                display = c
                break
    if not display and x_handle:
        display = x_handle
    if not display:
        display = short_addr or "Whale Entity"

    x_url = f"https://x.com/{x_handle}" if x_handle else ""
    return display, x_handle, x_url


def _num(val: Any, ndigits: int = 2) -> float | None:
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


def _mask_addr(addr: str) -> str:
    s = (addr or "").strip()
    if len(s) >= 12 and s.startswith("0x"):
        return f"{s[:6]}…{s[-4:]}"
    if len(s) >= 12:
        return f"{s[:5]}…{s[-4:]}"
    return s[:16]


def _clean_public_hl_name(hl_name: str | None, short_addr: str) -> str:
    """STRICT: Never fall back to internal entity_label. Use verified HL public display name or masked 0x."""
    s = re.sub(r"\s+", " ", str(hl_name or "")).strip()
    if s and len(s) <= 22 and not any(ch in s for ch in ("，", "。", "！", "？", "\n", ":", "/")) and not _BAD_PUBLIC_NAME_RE.search(s):
        return s
    return short_addr or "Whale Entity"


def _ship_class(notional_usd: float) -> tuple[str, str]:
    if notional_usd >= 50_000_000:
        return "CARRIER", "航空母舰"
    if notional_usd >= 10_000_000:
        return "CRUISER", "巡洋舰"
    return "DESTROYER", "驱逐舰"


def _connect_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn
    except sqlite3.Error:
        return None


def _safe_query(conn: sqlite3.Connection | None, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def empty_whales_payload() -> dict[str, Any]:
    return {
        "available": False,
        "updated_at": 0,
        "stats": {
            "tracked_addresses": 0,
            "profiled_whales": 0,
            "active_entities": 0,
            "total_active_notional_usd": 0.0,
            "x_linked_count": 0,
            "profile_coverage_pct": 0.0,
        },
        "cohorts": {},
        "series": {},
        "flagships": [],
    }


def classify_divergence(sa_lp: float, cc_lp: float) -> dict[str, Any]:
    """Classify Smart-vs-Counter positioning strictly respecting whether cohorts lie on opposite sides of 50%."""
    spread = round(sa_lp - cc_lp, 1)
    if sa_lp < 50.0 < cc_lp and spread <= -35.0:
        label, tone = "极度背离 · 聪明钱偏空 / 反指偏多", "danger"
    elif cc_lp < 50.0 < sa_lp and spread >= 35.0:
        label, tone = "极度背离 · 聪明钱偏多 / 反指偏空", "bullish"
    elif sa_lp < 50.0 < cc_lp and spread <= -15.0:
        label, tone = "方向分化 · 聪明钱偏空 / 反指偏多", "warn"
    elif cc_lp < 50.0 < sa_lp and spread >= 15.0:
        label, tone = "方向分化 · 聪明钱偏多 / 反指偏空", "bullish"
    elif abs(spread) >= 25.0:
        rel = "聪明钱多头占比显著低于反指" if spread < 0 else "聪明钱多头占比显著高于反指"
        label, tone = f"同向相对分歧 · {rel}", "warn"
    else:
        label, tone = "阵营接近 · 暂无显著背离", "neutral"
    return {
        "spread_pct": spread,
        "smart_long_pct": sa_lp,
        "counter_long_pct": cc_lp,
        "label": label,
        "tone": tone,
    }


def export_whales(db_path: Path, *, max_flagships: int = 40) -> dict[str, Any]:
    conn = _connect_ro(db_path)
    if conn is None:
        return empty_whales_payload()

    try:
        # 1. Global coverage stats
        addr_cnt = _safe_query(conn, "SELECT COUNT(*) AS c FROM address_registry")
        prof_cnt = _safe_query(conn, "SELECT COUNT(*) AS c FROM onchain_profiles WHERE hl_found = 1")
        tracked_addresses = _int_or_zero(addr_cnt[0]["c"]) if addr_cnt else 0
        profiled_whales = _int_or_zero(prof_cnt[0]["c"]) if prof_cnt else 0

        # 2. Latest cohort snapshots across ALL / BTC / ETH / HYPE / SOL
        ts_rows = _safe_query(conn, "SELECT MAX(ts) AS mx FROM live_ls_snapshots")
        latest_ls_ts = _int_or_zero(ts_rows[0]["mx"]) if ts_rows else 0

        cohorts_by_coin: dict[str, dict[str, Any]] = {}
        if latest_ls_ts > 0:
            ls_rows = _safe_query(
                conn,
                """
                SELECT cohort, coin, long_entities, short_entities, balanced_entities,
                       headcount_long_pct, long_usd, short_usd, net_usd, notional_long_pct,
                       btc_price, eth_price
                FROM live_ls_snapshots
                WHERE ts = ?
                """,
                (latest_ls_ts,),
            )
            for r in ls_rows:
                coin = str(r.get("coin") or "")
                coh = str(r.get("cohort") or "")
                if not coin or not coh:
                    continue
                bucket = cohorts_by_coin.setdefault(coin, {})
                bucket[coh] = {
                    "long_entities": max(0, _int_or_zero(r.get("long_entities"))),
                    "short_entities": max(0, _int_or_zero(r.get("short_entities"))),
                    "balanced_entities": max(0, _int_or_zero(r.get("balanced_entities"))),
                    "headcount_long_pct": _num(r.get("headcount_long_pct"), 1),
                    "long_usd": _num(r.get("long_usd"), 0),
                    "short_usd": _num(r.get("short_usd"), 0),
                    "net_usd": _num(r.get("net_usd"), 0),
                    "notional_long_pct": _num(r.get("notional_long_pct"), 1),
                }

            for coin, bucket in cohorts_by_coin.items():
                sa = bucket.get("SMART_SA") or {}
                cc = bucket.get("COUNTER_C") or {}
                sa_lp = sa.get("notional_long_pct")
                cc_lp = cc.get("notional_long_pct")
                if sa_lp is not None and cc_lp is not None:
                    bucket["divergence"] = classify_divergence(float(sa_lp), float(cc_lp))

        # 3. Time-series of Smart vs Counter long% across recent snapshots (up to 96 points)
        series_by_coin: dict[str, list[dict[str, Any]]] = {}
        hist_ls = _safe_query(
            conn,
            """
            SELECT ts, cohort, coin, notional_long_pct, net_usd, btc_price, eth_price
            FROM live_ls_snapshots
            WHERE cohort IN ('SMART_SA', 'COUNTER_C', 'ALL_DIRECTIONAL')
              AND ts >= ?
            ORDER BY ts ASC
            """,
            (max(0, latest_ls_ts - 7 * 86400),),
        )
        grid: dict[str, dict[int, dict[str, Any]]] = {}
        for r in hist_ls:
            coin = str(r.get("coin") or "")
            ts_v = _int_or_zero(r.get("ts"))
            coh = str(r.get("cohort") or "")
            if not coin or ts_v <= 0:
                continue
            pt = grid.setdefault(coin, {}).setdefault(
                ts_v,
                {
                    "ts": ts_v,
                    "smart_long_pct": None,
                    "counter_long_pct": None,
                    "smart_net_usd": None,
                    "counter_net_usd": None,
                    "btc_price": _num(r.get("btc_price"), 1),
                },
            )
            if coh == "SMART_SA":
                pt["smart_long_pct"] = _num(r.get("notional_long_pct"), 1)
                pt["smart_net_usd"] = _num(r.get("net_usd"), 0)
            elif coh == "COUNTER_C":
                pt["counter_long_pct"] = _num(r.get("notional_long_pct"), 1)
                pt["counter_net_usd"] = _num(r.get("net_usd"), 0)

        for coin, by_ts in grid.items():
            series_by_coin[coin] = [by_ts[k] for k in sorted(by_ts.keys())][-96:]

        # 4. Active entity flagships — FULL position accounting (no threshold mismatch!)
        pos_ts_rows = _safe_query(conn, "SELECT MAX(ts) AS mx FROM live_entity_pos_history")
        latest_pos_ts = _int_or_zero(pos_ts_rows[0]["mx"]) if pos_ts_rows else 0
        updated_at = max(latest_ls_ts, latest_pos_ts)

        flagships: list[dict[str, Any]] = []
        active_entity_count = 0
        total_active_notional = 0.0
        x_linked_count = 0
        full_cov_entities = 0

        if latest_pos_ts > 0:
            cur_pos_rows = _safe_query(
                conn,
                """
                SELECT entity_cluster_id, tier, coin, szi, signed_ntl_usd, mark_px
                FROM live_entity_pos_history
                WHERE ts = ?
                """,
                (latest_pos_ts,),
            )

            # Aggregate ALL positions for true entity net_usd & total_notional (matching historical SQL!)
            entities: dict[str, dict[str, Any]] = {}
            latest_coin_px: dict[str, float] = {}
            for r in cur_pos_rows:
                eid = str(r.get("entity_cluster_id") or "")
                coin = str(r.get("coin") or "")
                usd = _num(r.get("signed_ntl_usd"), 2)
                szi = _num(r.get("szi"), 8)
                try:
                    mpx_raw = float(r.get("mark_px")) if r.get("mark_px") is not None else None
                except (TypeError, ValueError):
                    mpx_raw = None
                if mpx_raw is not None and not math.isfinite(mpx_raw):
                    mpx_raw = None
                mpx = _num(mpx_raw, 6) if mpx_raw is not None else None
                if not eid:
                    continue
                ent = entities.setdefault(
                    eid,
                    {
                        "eid": eid,
                        "tier": str(r.get("tier") or "S_SMART_WHALE"),
                        "all_positions": [],
                        "cur_szi": {},
                        "cur_szi_complete": True,
                        "total_notional": 0.0,
                        "net_usd": 0.0,
                    },
                )
                if not coin or usd is None or szi is None:
                    ent["cur_szi_complete"] = False
                    if not coin or usd is None:
                        continue
                if mpx_raw is not None and mpx_raw > 0:
                    latest_coin_px[coin] = mpx_raw
                ent["total_notional"] += abs(usd)
                ent["net_usd"] += usd
                if szi is not None:
                    ent["cur_szi"][coin] = ent["cur_szi"].get(coin, 0.0) + szi
                if abs(usd) >= 50_000:
                    ent["all_positions"].append({
                        "coin": coin,
                        "side": "LONG" if usd > 0 else "SHORT",
                        "usd": round(abs(usd), 0),
                        "signed_usd": round(usd, 0),
                        "mark_px": _num(mpx, 4),
                    })

            # Keep entities with meaningful total exposure (>= $50K)
            active_ents = [e for e in entities.values() if e["total_notional"] >= 50_000 and e["all_positions"]]
            active_entity_count = len(active_ents)
            total_active_notional = round(sum(e["total_notional"] for e in active_ents), 0)

            top_entities = sorted(active_ents, key=lambda e: e["total_notional"], reverse=True)[:max_flagships]
            top_eids = [e["eid"] for e in top_entities]

            if top_eids:
                ph = ",".join("?" for _ in top_eids)
                # Dynamically detect optional enrichment columns; intentionally NEVER select private entity_label!
                a_cols = {str(r["name"]) for r in _safe_query(conn, "PRAGMA table_info(address_registry)")}
                o_cols = {str(r["name"]) for r in _safe_query(conn, "PRAGMA table_info(onchain_profiles)")}
                sel_ext = "a.external_labels_json" if "external_labels_json" in a_cols else "'[]'"
                sel_mpnl = "o.hl_month_pnl" if "hl_month_pnl" in o_cols else "NULL"
                sel_wpnl = "o.hl_week_pnl" if "hl_week_pnl" in o_cols else "NULL"
                sel_pos = "o.hl_positions_json" if "hl_positions_json" in o_cols else "'[]'"
                sel_hip3 = "o.hl_hip3_positions_json" if "hl_hip3_positions_json" in o_cols else "'[]'"
                sel_aster = "o.aster_positions_json" if "aster_positions_json" in o_cols else "'[]'"
                sel_lighter = "o.lighter_positions_json" if "lighter_positions_json" in o_cols else "'[]'"

                addr_rows = _safe_query(
                    conn,
                    f"""
                    SELECT a.entity_cluster_id AS eid,
                           a.identifier AS identifier,
                           {sel_ext} AS external_labels_json,
                           o.hl_display_name AS hl_display_name,
                           o.hl_alltime_pnl AS hl_alltime_pnl,
                           {sel_mpnl} AS hl_month_pnl,
                           {sel_wpnl} AS hl_week_pnl,
                           o.hl_account_value AS hl_account_value,
                           {sel_pos} AS hl_positions_json,
                           {sel_hip3} AS hl_hip3_positions_json,
                           {sel_aster} AS aster_positions_json,
                           {sel_lighter} AS lighter_positions_json
                    FROM address_registry a
                    LEFT JOIN onchain_profiles o ON o.identifier = a.identifier
                    WHERE a.entity_cluster_id IN ({ph})
                    """,
                    tuple(top_eids),
                )
                # Only load verified public leaderboard labels (strict source_name allowlist + table check + batched IN)
                all_top_ids = sorted({str(r["identifier"]) for r in addr_rows if r.get("identifier")})
                obs_by_id: dict[str, list[str]] = {}
                has_ext_obs = bool(_safe_query(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_observations'"))
                if all_top_ids and has_ext_obs:
                    for off in range(0, len(all_top_ids), 400):
                        chunk = all_top_ids[off : off + 400]
                        ph_ids = ",".join("?" for _ in chunk)
                        obs_rows = _safe_query(
                            conn,
                            f"""
                            SELECT identifier, display_label
                            FROM external_observations
                            WHERE identifier IN ({ph_ids})
                              AND source_name IN ('hl_leaderboard', 'wsm_hyperdash_seed')
                              AND display_label IS NOT NULL AND display_label != ''
                            """,
                            tuple(chunk),
                        )
                        for orow in obs_rows:
                            obs_by_id.setdefault(str(orow["identifier"]), []).append(str(orow["display_label"]))

                meta_map: dict[str, dict[str, Any]] = {}
                pos_detail_by_eid: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
                for r in addr_rows:
                    eid = str(r["eid"])
                    ident = str(r.get("identifier") or "")
                    mm = meta_map.setdefault(
                        eid,
                        {
                            "ids": set(),
                            "primary_addr": "",
                            "hl_name": "",
                            "ext_labels": [],
                            "obs_labels": [],
                            "pnl_cov": 0,
                            "val_cov": 0,
                            "month_cov": 0,
                            "week_cov": 0,
                            "alltime_pnl": 0.0,
                            "month_pnl": 0.0,
                            "week_pnl": 0.0,
                            "account_value": 0.0,
                        },
                    )
                    if ident and ident not in mm["ids"]:
                        mm["ids"].add(ident)
                        if not mm["primary_addr"] or ident < mm["primary_addr"]:
                            mm["primary_addr"] = ident
                        if r.get("hl_display_name") and not mm["hl_name"]:
                            mm["hl_name"] = str(r["hl_display_name"])
                        try:
                            el = json.loads(r.get("external_labels_json") or "[]")
                            if isinstance(el, list):
                                mm["ext_labels"].extend(str(x) for x in el if x)
                        except (TypeError, json.JSONDecodeError):
                            pass
                        mm["obs_labels"].extend(obs_by_id.get(ident, []))
                        if r.get("hl_alltime_pnl") is not None:
                            mm["pnl_cov"] += 1
                            mm["alltime_pnl"] += float(r["hl_alltime_pnl"])
                        if r.get("hl_month_pnl") is not None:
                            mm["month_cov"] += 1
                            mm["month_pnl"] += float(r["hl_month_pnl"])
                        if r.get("hl_week_pnl") is not None:
                            mm["week_cov"] += 1
                            mm["week_pnl"] += float(r["hl_week_pnl"])
                        if r.get("hl_account_value") is not None:
                            mm["val_cov"] += 1
                            mm["account_value"] += float(r["hl_account_value"])

                        # Merge detailed onchain entry_px / liq_px / upnl / lev across ALL venues with strict qty reconciliation!
                        ep_map = pos_detail_by_eid.setdefault(eid, {})
                        venue_cols = (
                            ("hl_positions_json", "HL"),
                            ("hl_hip3_positions_json", "HIP3"),
                            ("aster_positions_json", "Aster"),
                            ("lighter_positions_json", "Lighter"),
                        )
                        for col, venue_tag in venue_cols:
                            try:
                                plist = json.loads(r.get(col) or "[]")
                            except (TypeError, json.JSONDecodeError):
                                plist = []
                            if not isinstance(plist, list):
                                continue
                            for pp in plist:
                                if not isinstance(pp, dict):
                                    continue
                                cn = str(pp.get("coin") or "")
                                sd = str(pp.get("side") or "").upper()
                                ntl = abs(float(pp.get("ntl_usd") or 0.0))
                                if not cn or sd not in ("LONG", "SHORT") or ntl <= 0:
                                    continue
                                cur = ep_map.setdefault(
                                    (cn, sd),
                                    {
                                        "legs": 0,
                                        "szi_sum": 0.0,
                                        "venues": set(),
                                        "entry_qty_legs": 0,
                                        "entry_qty_sum": 0.0,
                                        "entry_cost_sum": 0.0,
                                        "upnl_legs": 0,
                                        "upnl_sum": 0.0,
                                        "tight_liq": None,
                                        "tight_lev": None,
                                        "lev_types": set(),
                                        "single_lev": None,
                                    },
                                )
                                cur["legs"] += 1
                                cur["venues"].add(venue_tag)
                                try:
                                    ep_raw = float(pp.get("entry_px") or 0.0)
                                except (TypeError, ValueError):
                                    ep_raw = 0.0
                                if not math.isfinite(ep_raw):
                                    ep_raw = 0.0
                                szi_abs = abs(float(pp.get("szi") or 0.0))
                                if szi_abs > 0:
                                    cur["szi_sum"] += szi_abs
                                if ep_raw > 0 and szi_abs > 0:
                                    cur["entry_qty_legs"] += 1
                                    cur["entry_qty_sum"] += szi_abs
                                    cur["entry_cost_sum"] += ep_raw * szi_abs
                                up = _num(pp.get("upnl"), 2)
                                if up is not None:
                                    cur["upnl_legs"] += 1
                                    cur["upnl_sum"] += up
                                lv = _num(pp.get("lev"), 1)
                                if cur["legs"] == 1:
                                    cur["single_lev"] = lv if (lv is not None and lv > 0) else None
                                lt = str(pp.get("lev_type") or "").strip()[:12]
                                if lt:
                                    cur["lev_types"].add(lt)
                                lq = _num(pp.get("liq_px"), 6)
                                if lq is not None and lq > 0:
                                    # Pick highest-risk (closest-to-mark) liquidation price across multi-address legs:
                                    # for LONG higher liq_px is tighter risk; for SHORT lower liq_px is tighter risk.
                                    is_tighter = (
                                        cur["tight_liq"] is None
                                        or (sd == "LONG" and lq > cur["tight_liq"])
                                        or (sd == "SHORT" and lq < cur["tight_liq"])
                                    )
                                    if is_tighter:
                                        cur["tight_liq"] = lq
                                        cur["tight_lev"] = lv if (lv is not None and lv > 0) else None

                # Full 7-day history for trajectory AND exact 24h baseline BEFORE slicing!
                hist_agg_rows = _safe_query(
                    conn,
                    f"""
                    SELECT ts, entity_cluster_id AS eid, SUM(signed_ntl_usd) AS net_usd, SUM(ABS(signed_ntl_usd)) AS gross_usd
                    FROM live_entity_pos_history
                    WHERE entity_cluster_id IN ({ph})
                      AND ts >= ?
                    GROUP BY ts, entity_cluster_id
                    ORDER BY ts ASC
                    """,
                    tuple(top_eids) + (max(0, latest_pos_ts - 7 * 86400),),
                )
                hist_map: dict[str, list[dict[str, Any]]] = {}
                for r in hist_agg_rows:
                    eid = str(r["eid"])
                    hist_map.setdefault(eid, []).append({
                        "ts": _int_or_zero(r["ts"]),
                        "net_usd": _num(r["net_usd"], 0),
                        "gross_usd": _num(r["gross_usd"], 0),
                    })

                # Select bounded baseline timestamp per entity from FULL history (BEFORE any slice!):
                # Prefer snapshot closest to 24h ago within [20h..28h]; else if accumulating history [1h..36h],
                # record exact delta_window_h honestly so short windows are NEVER misrepresented as 24h.
                ref_ts_by_eid: dict[str, int] = {}
                ref_span_h_by_eid: dict[str, float] = {}
                for eid, full_traj in hist_map.items():
                    if len(full_traj) < 2:
                        continue
                    cands_24h = [
                        p for p in full_traj
                        if 20 * 3600 <= (latest_pos_ts - p["ts"]) <= 28 * 3600
                    ]
                    if cands_24h:
                        ref_pt = min(cands_24h, key=lambda p: abs((latest_pos_ts - p["ts"]) - 86400))
                    else:
                        cands_recent = [
                            p for p in full_traj
                            if 3600 <= (latest_pos_ts - p["ts"]) <= 36 * 3600
                        ]
                        if not cands_recent:
                            continue
                        ref_pt = cands_recent[0]
                    ref_ts_by_eid[eid] = ref_pt["ts"]
                    ref_span_h_by_eid[eid] = round((latest_pos_ts - ref_pt["ts"]) / 3600.0, 1)

                # Fetch coin-level szi at each entity's exact reference timestamp (fail-closed if any szi is NULL)
                ref_ts_set = sorted(set(ref_ts_by_eid.values()))
                prev_szi_map: dict[tuple[str, int], dict[str, tuple[float, float]]] = {}
                prev_szi_incomplete: set[tuple[str, int]] = set()
                if ref_ts_set:
                    ph_ts = ",".join("?" for _ in ref_ts_set)
                    prev_rows = _safe_query(
                        conn,
                        f"""
                        SELECT ts, entity_cluster_id AS eid, coin, szi, mark_px
                        FROM live_entity_pos_history
                        WHERE entity_cluster_id IN ({ph})
                          AND ts IN ({ph_ts})
                        """,
                        tuple(top_eids) + tuple(ref_ts_set),
                    )
                    for r in prev_rows:
                        key = (str(r["eid"]), _int_or_zero(r["ts"]))
                        coin = str(r.get("coin") or "")
                        szi = _num(r.get("szi"), 8)
                        mpx = _num(r.get("mark_px"), 6)
                        if not coin or szi is None:
                            prev_szi_incomplete.add(key)
                            continue
                        bucket = prev_szi_map.setdefault(key, {})
                        old_szi, old_px = bucket.get(coin, (0.0, mpx or 0.0))
                        bucket[coin] = (old_szi + szi, mpx or old_px)

                for ent in top_entities:
                    eid = ent["eid"]
                    m = meta_map.get(eid) or {}
                    addr_count = max(1, len(m.get("ids") or []))
                    pnl_cov = _int_or_zero(m.get("pnl_cov"))
                    val_cov = _int_or_zero(m.get("val_cov"))
                    month_cov = _int_or_zero(m.get("month_cov"))
                    week_cov = _int_or_zero(m.get("week_cov"))
                    alltime_pnl = _num(m.get("alltime_pnl"), 0) if (pnl_cov == addr_count and pnl_cov > 0) else None
                    month_pnl = _num(m.get("month_pnl"), 0) if (month_cov == addr_count and month_cov > 0) else None
                    week_pnl = _num(m.get("week_pnl"), 0) if (week_cov == addr_count and week_cov > 0) else None
                    account_value = _num(m.get("account_value"), 0) if (val_cov == addr_count and val_cov > 0) else None
                    if pnl_cov == addr_count and pnl_cov > 0:
                        full_cov_entities += 1

                    short_addr = _mask_addr(str(m.get("primary_addr") or ""))
                    display, x_handle, x_url = _extract_verified_x_and_alias(
                        m.get("hl_name"),
                        m.get("ext_labels") or [],
                        m.get("obs_labels") or [],
                        short_addr,
                    )
                    if x_handle:
                        x_linked_count += 1
                    tier_raw = ent["tier"]
                    tier_code, tier_label, _ = TIER_META.get(tier_raw, ("W", "活跃巨鲸", 9))

                    ep_map = pos_detail_by_eid.get(eid) or {}
                    full_cluster_prof = pnl_cov == addr_count and pnl_cov > 0
                    positions = sorted(ent["all_positions"], key=lambda p: p["usd"], reverse=True)
                    for pos in positions:
                        det = ep_map.get((pos["coin"], pos["side"]))
                        opp_side = "SHORT" if pos["side"] == "LONG" else "LONG"
                        has_opp_leg = ep_map.get((pos["coin"], opp_side)) is not None
                        live_szi_abs = abs(float(ent["cur_szi"].get(pos["coin"], 0.0)))
                        prof_szi_abs = float(det["szi_sum"]) if det else 0.0
                        # Strict <=0.1% exact token-quantity reconciliation & no opposing-side hedge leg!
                        # Any newly opened/closed leg (>0.1% qty delta) fails closed rather than extrapolating stale cost.
                        qty_reconciled = (
                            not has_opp_leg
                            and live_szi_abs > 0
                            and prof_szi_abs > 0
                            and (abs(prof_szi_abs - live_szi_abs) / live_szi_abs) <= 0.001
                        )
                        if det and det["legs"] > 0 and full_cluster_prof and qty_reconciled:
                            venues = sorted(det["venues"])
                            single_venue = len(venues) == 1
                            if det["entry_qty_legs"] == det["legs"] and det["entry_qty_sum"] > 0:
                                entry_px_raw = det["entry_cost_sum"] / det["entry_qty_sum"]
                                entry_px = _num(entry_px_raw, 6 if entry_px_raw < 1.0 else 4)
                            else:
                                entry_px_raw = None
                                entry_px = None
                            pos["entry_px"] = entry_px
                            # Full-precision live mark-to-market uPnL before display rounding (prevents low-price meme rounding loss!)
                            mpx_raw = latest_coin_px.get(pos["coin"]) or pos.get("mark_px")
                            if entry_px_raw is not None and mpx_raw is not None and float(mpx_raw) > 0:
                                diff_px = (float(mpx_raw) - entry_px_raw) if pos["side"] == "LONG" else (entry_px_raw - float(mpx_raw))
                                pos["upnl"] = _num(diff_px * live_szi_abs, 0)
                            elif det["upnl_legs"] == det["legs"]:
                                pos["upnl"] = _num(det["upnl_sum"], 0)
                            else:
                                pos["upnl"] = None
                            # Never display single-venue liq_px / lev as if it governed a cross-venue sum
                            if single_venue:
                                pos["liq_px"] = _num(det["tight_liq"], 6 if (det["tight_liq"] and det["tight_liq"] < 1.0) else 4) if det["tight_liq"] else None
                                if det["tight_liq"]:
                                    pos["lev"] = _num(det["tight_lev"], 1) if det["tight_lev"] else None
                                elif det["legs"] == 1:
                                    pos["lev"] = _num(det["single_lev"], 1) if det["single_lev"] else None
                                else:
                                    pos["lev"] = None
                                ltypes = det["lev_types"]
                                pos["lev_type"] = next(iter(ltypes)) if len(ltypes) == 1 else ("mixed" if len(ltypes) > 1 else "")
                            else:
                                pos["liq_px"] = None
                                pos["lev"] = None
                                pos["lev_type"] = ""
                            pos["legs"] = det["legs"]
                            pos["venues"] = venues
                        else:
                            pos["entry_px"] = None
                            pos["liq_px"] = None
                            pos["upnl"] = None
                            pos["lev"] = None
                            pos["lev_type"] = ""
                            pos["legs"] = 0
                            pos["venues"] = []
                    primary = positions[0]
                    ship_code, ship_label = _ship_class(ent["total_notional"])

                    full_traj = hist_map.get(eid, [])
                    if len(full_traj) > 32:
                        step = (len(full_traj) - 1) / 31.0
                        chart_traj = [full_traj[round(i * step)] for i in range(32)]
                    else:
                        chart_traj = full_traj

                    # Constant-price quantity delta attribution (fail-closed if any szi missing or outside window!)
                    delta_usd: float | None = None
                    delta_window_h: float | None = None
                    action = "持仓"
                    if eid in ref_ts_by_eid and ent["cur_szi_complete"]:
                        rts = ref_ts_by_eid[eid]
                        if (eid, rts) not in prev_szi_incomplete and (eid, rts) in prev_szi_map:
                            prev_coins = prev_szi_map[(eid, rts)]
                            cur_coins = ent["cur_szi"]
                            all_coins = set(prev_coins.keys()) | set(cur_coins.keys())
                            qty_flow = 0.0
                            prev_const_net = 0.0
                            cur_const_net = 0.0
                            for cn in all_coins:
                                c_szi = cur_coins.get(cn, 0.0)
                                p_szi, p_px = prev_coins.get(cn, (0.0, 0.0))
                                px_ref = latest_coin_px.get(cn) or p_px or 0.0
                                qty_flow += (c_szi - p_szi) * px_ref
                                prev_const_net += p_szi * px_ref
                                cur_const_net += c_szi * px_ref
                            delta_usd = round(qty_flow, 0)
                            delta_window_h = ref_span_h_by_eid[eid]
                            if prev_const_net * cur_const_net < 0 and abs(cur_const_net) >= 1_000_000 and abs(delta_usd) >= 1_000_000:
                                action = "反手"
                            elif abs(delta_usd) >= max(500_000.0, abs(prev_const_net) * 0.05):
                                if (cur_const_net >= 0 and delta_usd > 0) or (cur_const_net < 0 and delta_usd < 0):
                                    action = "加仓"
                                else:
                                    action = "减仓"

                    flagships.append({
                        "id": f"W-{eid[-6:].upper()}",
                        "display": display,
                        "short_addr": short_addr,
                        "x_handle": x_handle,
                        "x_url": x_url,
                        "addr_count": addr_count,
                        "profile_cov": f"{pnl_cov}/{addr_count}",
                        "tier": tier_raw,
                        "tier_code": tier_code,
                        "tier_label": tier_label,
                        "ship_class": ship_code,
                        "ship_label": ship_label,
                        "primary_coin": primary["coin"],
                        "primary_side": primary["side"],
                        "primary_usd": primary["usd"],
                        "total_notional_usd": round(ent["total_notional"], 0),
                        "net_usd": round(ent["net_usd"], 0),
                        "delta_usd": delta_usd,
                        "delta_window_h": delta_window_h,
                        "action": action,
                        "alltime_pnl": alltime_pnl,
                        "month_pnl": month_pnl,
                        "week_pnl": week_pnl,
                        "account_value": account_value,
                        "positions": positions[:8],
                        "history": chart_traj,
                    })

        cov_pct = round((full_cov_entities / len(flagships)) * 100.0, 1) if flagships else 0.0
        return {
            "available": bool(cohorts_by_coin or flagships),
            "updated_at": updated_at,
            "stats": {
                "tracked_addresses": tracked_addresses,
                "profiled_whales": profiled_whales,
                "active_entities": active_entity_count,
                "total_active_notional_usd": total_active_notional,
                "x_linked_count": x_linked_count if flagships else 0,
                "profile_coverage_pct": cov_pct,
            },
            "cohorts": cohorts_by_coin,
            "series": series_by_coin,
            "flagships": flagships,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export privacy-safe public whale arena snapshot")
    parser.add_argument("--db", type=Path, required=True, help="Path to whale_intel.db")
    parser.add_argument("--out", type=Path, help="Optional output JSON path")
    parser.add_argument("--blocklist", type=Path, default=None, help="Optional private blocklist file")
    parser.add_argument("--require-blocklist", action="store_true", help="Fail closed if blocklist missing")
    args = parser.parse_args()

    # Fail-closed: NEVER write or print output if privacy_guard is unavailable
    if _pg is None:
        raise RuntimeError("缺少 privacy_guard 模块，拒绝绕过隐私校验导出")

    now_dt = datetime.now(BJT)
    payload = {
        "meta": {
            "project": "DanClub Open Terminal",
            "license": "MIT",
            "generated_at_bjt": now_dt.strftime("%Y-%m-%d %H:%M:%S +08:00"),
            "generated_ts": int(now_dt.timestamp()),
        },
        **export_whales(args.db),
    }
    _pg.assert_clean_payload(payload, blocklist_path=args.blocklist, require_blocklist=args.require_blocklist)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
