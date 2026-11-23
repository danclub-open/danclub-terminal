# DanClub Terminal Open API Reference (`v1`)

All endpoints are **static, read-only, unauthenticated JSON** with `Access-Control-Allow-Origin: *`.

- **Primary Fast Mirror (3-min cadence):** `https://danclub.duckdns.org/api/v1`
- **GitHub Pages Mirror (15-min cadence):** `https://danclub-open.github.io/danclub-terminal/api/v1`

---

## 1. `GET /index.json`
Manifest listing generation timestamps, schema version, and available endpoints.

```json
{
  "meta": {
    "schema_version": "2.0",
    "generated_at_bjt": "2026-11-23 16:45:00 +08:00",
    "generated_at_ts": 1795423500
  },
  "endpoints": {
    "overview": "overview.json",
    "whales": "whales.json",
    "arb": "arb.json",
    "flows": "flows.json",
    "labs": "labs.json",
    "feed": "feed.json"
  }
}
```

---

## 2. `GET /whales.json`
Entity-deduplicated whale positioning across Hyperliquid & HIP-3 (`xyz:`) TradFi perpetuals.

- `stats`: `{ tracked_addresses, profiled_whales, active_entities, total_notional_usd, x_linked_count, snapshot_ts }`
- `cohorts[coin]`: `ALL`, `BTC`, `ETH`, `HYPE`, `SOL` -> `{ SMART_SA, COUNTER_C, ALL_DIRECTIONAL, divergence }`, each cohort containing:
  - `long_entities`, `short_entities`, `balanced_entities`, `headcount_long_pct`, `long_usd`, `short_usd`, `net_usd`, `notional_long_pct`
  - `divergence`: `{ smart_long_pct, counter_long_pct, spread_pct, tone, label }`
- `series[coin][]`: 15-minute historical snapshots `{ ts, smart_long_pct, counter_long_pct, smart_net_usd, counter_net_usd, btc_price }`
- `flagships[]`: Top 40 active deduplicated directional whale entities:
  - `id`, `display`, `short_addr`, `addr_count`
  - `x_handle` *(string or `null`, verified public X handle without `@`)*, `x_url` *(`https://x.com/<handle>` or `""`)*
  - `tier_code` (`S`/`A`/`C`/`F`), `tier_label`, `ship_class` (`CARRIER`/`CRUISER`/`DESTROYER`)
  - `primary_coin`, `primary_side` (`LONG`/`SHORT`), `primary_usd`
  - `total_notional_usd`, `net_usd`, `delta_usd` *(constant-price quantity change)*, `delta_window_h`, `action` (`持仓`/`加仓`/`减仓`/`反手`)
  - `alltime_pnl`, `month_pnl`, `week_pnl`, `account_value` *(null unless 100% cluster profile coverage)*
  - `positions[]`: `{ coin, side, usd, entry_px, liq_px, upnl, lev, lev_type }`
  - `history[]`: `{ ts, net_usd, total_usd }`

---

## 3. `GET /overview.json`
Cross-market trader decision layer.

- `market_regime`: `{ tone, label, hint, bottom_score, fng, vix, us10y }`
- `desk_signals[]`: Up to 5 high-priority cross-market alerts `{ severity, tag, metric, title, detail, target_tab }`
- `bottom_dashboard`: 7-point bottom confirmation checklist (`green_count`, `yellow_count`, `red_count`, `items[]`)
- `fear_greed`, `stablecoins` (`DeFiLlama` global scope), `tga`, `btc_miner`, `us10y_history[]`
- `macro_quotes[]`: US equities, FX, metals, volatility. During US equity off-hours, covered stocks switch primary price to Binance Spot `bStocks` (`price_source: "BINANCE_BSTOCKS"`, `session: "BSTOCKS_247"`) while preserving `reg_px` & `reg_chg_pct` (last official NYSE/Nasdaq regular close).
- `qdii_premium`: A-share QDII ETF premium-over-IOPV risk ranking.

---

## 4. `GET /arb.json`
- `spreads`: `SKHY_VS_SKHYNIX`, `XAU_VS_XAUT`, `XAU_VS_PAXG` (`latest`, `stats_72h`, `series_24h`, `daily_14d`)
- `csi_basis`: CFFEX `IC/IM/IF/IH` index futures basis term structure & annualized discount (`contracts[]`)
- `wallet_status`: 4-exchange (Binance / OKX / Bitget / Gate) deposit/withdrawal halt radar (`ok`, `summary[]`, `major_alerts[]`)

---

## 5. `GET /flows.json`
- `etf_flows`: US Spot BTC/ETH ETF daily issuer-level net flows, streak, best/worst days
- `mstr`: Official BTC holdings, SEC 8-K cost basis, 7d mNAV curve
- `hyperliquid`: Platform OI, volume, Assistance Fund HYPE holdings, top markets
- `bn_oi_radar[]`: Binance USDT-M 1h OI surge & extreme positive/negative funding radar

---

## 6. `GET /labs.json`
- `dca_simulation`: Transparent virtual paper-trading DCA comparison (`FIXED`, `VA`, `MAFG`)
- `exchange_promos[]`: Curated active public exchange campaigns (auto-purged expired items, referral params stripped)

---

## 7. `GET /feed.json`
Multi-source categorized real-time market wire (fusing Equation News `eqn.news` public channels + global macro/crypto wires + exchange announcements).

- `items[]`: `{ ts, dt_bjt, cat_code, category, source, headline, body, impact_label, impact_tone, coins[], url }`
  - `cat_code`: `CRYPTO` | `MACRO` | `LISTING` | `MOVES` | `INTEL`
