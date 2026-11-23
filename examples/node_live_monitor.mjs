#!/usr/bin/env node
/**
 * Zero-dependency Node.js (v18+) example for DanClub Terminal Public Open API.
 * Usage: node examples/node_live_monitor.mjs
 */

const BASE = "https://danclub.duckdns.org/api/v1";

async function getJson(path) {
  const res = await fetch(`${BASE}/${path}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${path} HTTP ${res.status}`);
  return res.json();
}

const [overview, whales, feed] = await Promise.all([
  getJson("overview.json"),
  getJson("whales.json"),
  getJson("feed.json"),
]);

console.log("=== DanClub Terminal Live Snapshot ===");
console.log("Generated:", overview.meta?.generated_at_bjt);
console.log("Market Regime:", overview.market_regime?.label);
console.log("BTC Whale Divergence:", whales.divergence?.BTC);

const bstocks = (overview.macro_quotes || []).filter((q) => q.price_source === "BINANCE_BSTOCKS");
if (bstocks.length) {
  console.log("\n24/7 Binance Spot bStocks (US Off-Hours):");
  bstocks.slice(0, 6).forEach((q) => {
    console.log(`  ${q.symbol.padEnd(6)} $${q.price} (${q.chg_pct >= 0 ? "+" : ""}${q.chg_pct}%) [${q.bstock_symbol}]`);
  });
}

console.log("\nLatest 5 Market Wires:");
(feed.items || []).slice(0, 5).forEach((it) => {
  console.log(`  [${it.category}] (${it.source}) ${it.title}`);
});
