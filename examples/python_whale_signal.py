#!/usr/bin/env python3
"""Zero-dependency Python example for DanClub Terminal Public Open API.

Usage:
    python3 examples/python_whale_signal.py
"""

from __future__ import annotations

import json
import urllib.request

BASE_URL = "https://danclub.duckdns.org/api/v1"
FALLBACK_URL = "https://danclub-open.github.io/danclub-terminal/api/v1"


def fetch_json(endpoint: str) -> dict:
    for base in (BASE_URL, FALLBACK_URL):
        try:
            req = urllib.request.Request(
                f"{base}/{endpoint}",
                headers={"User-Agent": "DanClub-API-Example/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
    raise RuntimeError(f"Failed to fetch {endpoint} from both primary and mirror endpoints")


def main() -> None:
    whales = fetch_json("whales.json")
    overview = fetch_json("overview.json")

    regime = overview.get("market_regime") or {}
    print(f"[Regime] {regime.get('label', '--')} | {regime.get('hint', '')}")

    div = (whales.get("divergence") or {}).get("BTC") or {}
    if div:
        print(
            f"[Whale BTC Divergence] Smart={div.get('smart_long_pct')}% Long vs "
            f"Counter={div.get('counter_long_pct')}% Long | Diff={div.get('diff_pct'):+.1f}% ({div.get('label')})"
        )

    print("\nTop 8 Active Whale Flagships:")
    for f in (whales.get("flagships") or [])[:8]:
        x_str = f" | X: @{f['x_handle']}" if f.get("x_handle") else ""
        print(
            f"  - {f['display']:16s} [{f['tier_code']}] {f['primary_coin']:12s} {f['primary_side']:5s} "
            f"Total=${f['total_notional_usd']/1e6:6.2f}M ({f['action']}){x_str}"
        )


if __name__ == "__main__":
    main()
