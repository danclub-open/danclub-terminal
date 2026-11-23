#!/usr/bin/env python3
"""Comprehensive regression tests for DanClub Open Terminal (privacy, data integrity, staging, XSS guards)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "exporter"))

import privacy_guard as pg  # noqa: E402
import public_terminal_exporter as exp  # noqa: E402
import sync_public_data as syncer  # noqa: E402


class TestPrivacyGuard(unittest.TestCase):
    def test_clean_public_payload_passes(self) -> None:
        payload = {
            "meta": {"project": "DanClub Open Terminal", "license": "MIT"},
            "overview": {"btc": 61234.5, "note": "现货 ETF 净流出收窄"},
            "links": ["https://www.binance.com/en/support/announcement"],
        }
        pg.assert_clean_payload(payload, blocklist_path=Path("/nonexistent/blocklist.txt"))

    def test_blocks_secrets_emails_ips_keys_and_custom_blocklist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bl = Path(td) / "local_blocklist.txt"
            bl.write_text("# test private literals\n203.0.113.42\ninternal-group-999\n", encoding="utf-8")
            bad_samples = [
                {"x": "-----BEGIN OPENSSH PRIVATE KEY-----"},
                {"x": "ghp_1234567890abcdefghijklmnopqrstuv"},
                {"x": "contact alice_demo@163.com"},
                {"x": "internal host 192.168.1.99"},
                {"x": "host ip 203.0.113.42"},
                {"x": "path /Users/demo/.secrets/totp_vault.json"},
                {"x": "我的个人持仓成本 60000"},
                {"x": "转发到 internal-group-999"},
                {"x": "javascript:alert(1)"},
                {"secret_key": "anything"},
                {"alice_demo@163.com": 1.23},  # forbidden string inside dict key!
            ]
            for sample in bad_samples:
                with self.assertRaises(pg.PrivacyViolationError, msg=f"Should block: {sample}"):
                    pg.assert_clean_payload(sample, blocklist_path=bl)

    def test_sanitize_public_url_enforces_https_no_userinfo_and_domain_allowlist(self) -> None:
        self.assertEqual(pg.sanitize_public_url("http://www.binance.com/a"), "")
        self.assertEqual(pg.sanitize_public_url("https://user:pass@www.binance.com/a"), "")
        self.assertEqual(pg.sanitize_public_url("https://[bad-ipv6"), "")
        self.assertEqual(
            pg.sanitize_public_url("https://evil.example.com/promo", allowed_domains=pg.ALLOWED_PROMO_DOMAINS),
            "",
        )
        cleaned = pg.sanitize_public_url(
            "https://www.okx.com/campaigns/earn?id=123&token=secret&ref=evil99#top",
            allowed_domains=pg.ALLOWED_PROMO_DOMAINS,
        )
        self.assertEqual(cleaned, "https://www.okx.com/campaigns/earn")

    def test_repo_url_redaction(self) -> None:
        self.assertEqual(
            syncer._redact_repo_url("https://user:ghp_secret123@github.com/org/repo.git"),
            "https://***@github.com/org/repo.git",
        )


class TestExporterIntegrityAndStaging(unittest.TestCase):
    def _seed_dbs(self, d: Path, *, include_wallet: bool = True, dca_days: int = 95) -> None:
        c = sqlite3.connect(d / "macro_market.db")
        c.execute("CREATE TABLE dashboard_state (item_key TEXT, status TEXT, detail TEXT, updated_at INT)")
        c.execute("INSERT INTO dashboard_state VALUES ('btc_low', 'GREEN', 'BTC 守住前低', 1795415000)")
        c.execute("CREATE TABLE kv_state (k TEXT, v TEXT)")
        c.execute("INSERT INTO kv_state VALUES ('fng_cache', '[15, \"Extreme Fear\"]')")
        c.execute("CREATE TABLE etf_flows (asset TEXT, trade_date TEXT, total_musd REAL, detail TEXT)")
        c.execute("INSERT INTO etf_flows VALUES ('BTC', '2026-11-20', -124.5, 'IBIT -50.0 | FBTC -74.5')")
        c.execute("CREATE TABLE quotes_60m (symbol TEXT, ts_sec INT, price REAL, oi_usd REAL)")
        c.execute("INSERT INTO quotes_60m VALUES ('SPY', 1795300000, 580.0, NULL)")
        c.execute("CREATE TABLE us10y_history (date_bjt TEXT, yield_pct REAL)")
        start = date(2026, 8, 1)
        for i in range(20):
            c.execute("INSERT INTO us10y_history VALUES (?, ?)", ((start + timedelta(days=i)).isoformat(), 4.2 + i * 0.01))
        c.commit()
        c.close()

        # spread_arb.db with NULL sum_spread_pct + 20 daily rows
        cs = sqlite3.connect(d / "spread_arb.db")
        cs.execute("CREATE TABLE spread_ticks (pair_id TEXT, ts INT, px_a REAL, px_b_raw REAL, px_b_adj REAL, spread_pct REAL, spread_usd REAL)")
        cs.execute("INSERT INTO spread_ticks VALUES ('SKHY_VS_SKHYNIX', 1795415000, 800, 112, 1120, 40.0, 320)")
        cs.execute("CREATE TABLE spread_daily (date_bjt TEXT, pair_id TEXT, samples INT, last_px_a REAL, last_px_b_raw REAL, last_px_b_adj REAL, last_spread_pct REAL, min_spread_pct REAL, max_spread_pct REAL, sum_spread_pct REAL)")
        for i in range(20):
            cs.execute(
                "INSERT INTO spread_daily VALUES (?, 'SKHY_VS_SKHYNIX', 100, 800, 112, 1120, ?, 38.0, 41.0, ?)",
                ((start + timedelta(days=i)).isoformat(), 39.0 + i * 0.05, None if i == 19 else 3900.0),
            )
        cs.commit()
        cs.close()

        # dca_sim.db with >90 days + one corrupt JSON 'null' row to prove fault isolation & DESC LIMIT 90
        cd = sqlite3.connect(d / "dca_sim.db")
        cd.execute("CREATE TABLE runs (date_bjt TEXT, created_at INT, summary_json TEXT, msg TEXT)")
        for i in range(dca_days):
            dt = (start + timedelta(days=i)).isoformat()
            sj = json.dumps({"FIXED": {"in": 100 * (i + 1), "mv": 105 * (i + 1), "pnl": 5 * (i + 1), "ret": 5.0, "days": i + 1, "per": {"BTC": 5.0}}})
            cd.execute("INSERT INTO runs VALUES (?, 1795415000, ?, '')", (dt, sj))
        cd.execute("INSERT INTO runs VALUES ('2026-07-01', 1795000000, 'null', '')")
        cd.commit()
        cd.close()

        if include_wallet:
            co = sqlite3.connect(d / "opportunity.db")
            co.execute("CREATE TABLE wallet_halt_state (ex TEXT, coin TEXT, dep_ok INT, wd_ok INT, halted_since INT, updated_at INT)")
            co.execute("CREATE TABLE bn_oi_latest (symbol TEXT, base TEXT, price REAL, oi_usd REAL, vol_24h REAL, chg_1h_oi REAL, chg_1h_px REAL, funding_rate REAL, updated_at INT)")
            co.commit()
            co.close()

    def test_day_95_dca_and_latest_14d_spreads_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            self._seed_dbs(data_dir, include_wallet=False, dca_days=95)
            bundle = exp.build_bundle(data_dir)
            # 95th day (index 94 from 2026-08-01 = 2026-11-03) MUST be latest_date!
            expected_last = (date(2026, 8, 1) + timedelta(days=94)).isoformat()
            self.assertEqual(bundle["labs"]["dca_simulation"]["latest_date"], expected_last)
            self.assertEqual(len(bundle["labs"]["dca_simulation"]["history"]), 90)
            self.assertEqual(bundle["labs"]["dca_simulation"]["strategies"]["FIXED"]["days"], 95)
            # Latest 14d spread_daily ends on 20th day (2026-08-20) and survives NULL sum_spread_pct
            daily = bundle["arb"]["spreads"]["SKHY_VS_SKHYNIX"]["daily_14d"]
            self.assertEqual(len(daily), 14)
            self.assertEqual(daily[-1]["date"], "2026-08-20")
            # Wallet DB missing -> ok MUST be False (fail-closed, never claim normal!)
            self.assertFalse(bundle["arb"]["wallet_status"]["ok"])

    def test_wrong_type_count_field_rejected_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            self._seed_dbs(data_dir, include_wallet=False, dca_days=3)
            bundle = exp.build_bundle(data_dir)
            bundle["arb"]["wallet_status"]["summary"].append({
                "exchange": "BINANCE",
                "total_coins": "<img src=x onerror=alert(1)>",
                "both_halted": 0,
                "dep_only_halted": 0,
                "wd_only_halted": 0,
            })
            with self.assertRaises(ValueError):
                syncer._clean_bundle(bundle, blocklist_path=Path("/nonexistent"))

    def test_staging_rejects_symlinks_and_matches_verified_digests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            self._seed_dbs(data_dir, include_wallet=False, dca_days=3)
            bundle = syncer._clean_bundle(exp.build_bundle(data_dir), blocklist_path=Path("/nonexistent"))
            api_bytes = syncer.build_verified_api_bytes(bundle, blocklist_path=Path("/nonexistent"))
            digests = syncer.publish_gh_pages(
                ROOT / "site",
                api_bytes,
                "git@github.com:danclub-open/danclub-terminal.git",
                dry_run_stage_only=True,
            )
            self.assertEqual(len(digests), 6)
            for name, raw in api_bytes.items():
                self.assertEqual(digests[name], hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    unittest.main()
