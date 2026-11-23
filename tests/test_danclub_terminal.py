#!/usr/bin/env python3
"""Comprehensive regression tests for DanClub Terminal (privacy, data integrity, staging, XSS & static deploy guards)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tarfile
import io
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "exporter"))

import privacy_guard as pg  # noqa: E402
import public_terminal_exporter as exp  # noqa: E402
import sync_public_data as syncer  # noqa: E402
import whale_public_exporter as wexp  # noqa: E402

BJT = timezone(timedelta(hours=8))


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
            bl.chmod(0o600)
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
                {"alice_demo@163.com": 1.23},
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


class TestExporterIntegrityAndHygiene(unittest.TestCase):
    def _seed_dbs(self, d: Path, *, include_wallet: bool = True, dca_days: int = 95) -> None:
        now_dt = datetime(2026, 11, 23, 15, 30, tzinfo=BJT)
        now_ts = int(now_dt.timestamp())

        c = sqlite3.connect(d / "macro_market.db")
        c.execute("CREATE TABLE dashboard_state (item_key TEXT, status TEXT, detail TEXT, updated_at INT)")
        c.execute("INSERT INTO dashboard_state VALUES ('btc_low', 'GREEN', 'BTC 守住前低', ?)", (now_ts,))
        c.execute("INSERT INTO dashboard_state VALUES ('stables', 'YELLOW', '收缩放缓：总计 $243.23B', ?)", (now_ts,))
        c.execute("CREATE TABLE kv_state (k TEXT, v TEXT)")
        c.execute("INSERT INTO kv_state VALUES ('fng_cache', '[11, \"Extreme Fear\"]')")
        c.execute("INSERT INTO kv_state VALUES ('stable_cache', '{\"total_b\": 282.67, \"chg_1d_b\": 0.18, \"chg_7d_b\": -4.43, \"chg_30d_b\": -18.0}')")
        c.execute("INSERT INTO kv_state VALUES ('tga_cache', '{\"date\": \"2026-11-19\", \"bal_b\": 891.2, \"chg_5d_b\": -41.9}')")
        c.execute(
            "INSERT INTO kv_state VALUES ('cnbc_quotes_latest', ?)",
            (
                json.dumps(
                    {
                        "ts": now_ts - 60,
                        "quotes": {
                            "NVDA": {
                                "reg_px": 125.53,
                                "reg_chg": -14.0,
                                "mkt_status": "PRE_MKT",
                                "ext_px": 130.01,
                                "ext_chg": 3.57,
                            },
                            "SPY": {
                                "reg_px": 689.81,
                                "reg_chg": -3.7,
                                "mkt_status": "CLOSED",
                                "ext_px": None,
                                "ext_chg": None,
                            },
                            "US10Y": {"reg_px": 4.948, "reg_chg": -0.04, "mkt_status": "REG_MKT"},
                        },
                    }
                ),
            ),
        )
        c.execute(
            "INSERT INTO kv_state VALUES ('drawdown_valuation_snapshot', ?)",
            (json.dumps({"ts": now_ts - 60, "drawdowns": {"NVDA": {"px": 125.53, "yrhi": 243.37, "dd_pct": -48.42}}}),),
        )
        c.execute(
            "INSERT INTO kv_state VALUES ('qdii_premium_snapshot', ?)",
            (json.dumps({"ts": now_ts - 60, "items": [{"code": "159509", "name": "纳指科技ETF", "px": 2.64, "iopv": 1.83, "chg_pct": -1.5, "prem_pct": 44.32}]}),),
        )
        c.execute("CREATE TABLE etf_flows (asset TEXT, trade_date TEXT, total_musd REAL, detail TEXT)")
        c.execute("INSERT INTO etf_flows VALUES ('BTC', '2026-11-18', -306.5, 'IBIT -148.7 | FBTC -69.3')")
        c.execute("INSERT INTO etf_flows VALUES ('BTC', '2026-11-19', -582.3, 'IBIT -541.1 | FBTC -45.9')")
        c.execute("INSERT INTO etf_flows VALUES ('BTC', '2026-11-20', -124.5, 'IBIT -50.0 | FBTC -74.5')")
        c.execute("CREATE TABLE quotes_60m (symbol TEXT, ts_sec INT, price REAL, oi_usd REAL)")
        c.execute("INSERT INTO quotes_60m VALUES ('SPY', ?, 689.81, NULL)", (now_ts,))
        c.execute("CREATE TABLE us10y_history (date_bjt TEXT, yield_pct REAL)")
        start = date(2026, 8, 1)
        for i in range(20):
            c.execute("INSERT INTO us10y_history VALUES (?, ?)", ((start + timedelta(days=i)).isoformat(), 4.2 + i * 0.01))
        c.commit()
        c.close()

        cs = sqlite3.connect(d / "spread_arb.db")
        cs.execute("CREATE TABLE spread_ticks (pair_id TEXT, ts INT, px_a REAL, px_b_raw REAL, px_b_adj REAL, spread_pct REAL, spread_usd REAL)")
        cs.execute("INSERT INTO spread_ticks VALUES ('SKHY_VS_SKHYNIX', ?, 800, 112, 1120, 40.0, 320)", (now_ts,))
        cs.execute("CREATE TABLE spread_daily (date_bjt TEXT, pair_id TEXT, samples INT, last_px_a REAL, last_px_b_raw REAL, last_px_b_adj REAL, last_spread_pct REAL, min_spread_pct REAL, max_spread_pct REAL, sum_spread_pct REAL)")
        for i in range(20):
            cs.execute(
                "INSERT INTO spread_daily VALUES (?, 'SKHY_VS_SKHYNIX', 100, 800, 112, 1120, ?, 38.0, 41.0, ?)",
                ((start + timedelta(days=i)).isoformat(), 39.0 + i * 0.05, None if i == 19 else 3900.0),
            )
        cs.commit()
        cs.close()

        cd = sqlite3.connect(d / "dca_sim.db")
        cd.execute("CREATE TABLE runs (date_bjt TEXT, created_at INT, summary_json TEXT, msg TEXT)")
        for i in range(dca_days):
            dt = (start + timedelta(days=i)).isoformat()
            sj = json.dumps({
                "FIXED": {"in": 100 * (i + 1), "mv": 105 * (i + 1), "pnl": 5 * (i + 1), "ret": 5.0, "days": i + 1, "per": {"BTC": 5.0}},
                "VA": {"in": 102 * (i + 1), "mv": 108 * (i + 1), "pnl": 6 * (i + 1), "ret": 5.88, "days": i + 1, "per": {"BTC": 5.8}},
                "MAFG": {"in": 120 * (i + 1), "mv": 128 * (i + 1), "pnl": 8 * (i + 1), "ret": 6.67, "days": i + 1, "per": {"BTC": 6.6}},
            })
            cd.execute("INSERT INTO runs VALUES (?, ?, ?, '')", (dt, now_ts, sj))
        cd.execute("INSERT INTO runs VALUES ('2026-07-01', 1795000000, 'null', '')")
        cd.commit()
        cd.close()

        cp = sqlite3.connect(d / "exchange_promos.db")
        cp.execute("CREATE TABLE promos (uid TEXT, exchange TEXT, promo_type TEXT, worth_level TEXT, title TEXT, url TEXT, summary_text TEXT, created_at INT)")
        # 1. Expired 2025 relic -> MUST BE DROPPED
        cp.execute(
            "INSERT INTO promos VALUES ('old1', '币安 Binance', '空投', '🔥🔥 重点可撸', 'Solayer HODLer Airdrop (2025-06-16)', 'https://www.binance.com/en/a', '旧活动', ?)",
            (now_ts - 100,),
        )
        # 2. Past deadline 2026-11-18 -> MUST BE DROPPED
        cp.execute(
            "INSERT INTO promos VALUES ('old2', '币安 Binance', '打新', '🔥🔥 重点可撸', 'Anthropic IPO Campaign', 'https://www.binance.com/en/b', '⏰ 活动时间：11-18 14:00 ~ 11-18 22:00 (北京时间)', ?)",
            (now_ts - 100,),
        )
        # 3. Active future promo -> MUST SURVIVE WITH CLEAN STRUCTURED FIELDS
        cp.execute(
            "INSERT INTO promos VALUES ('ok1', '币安 Binance', '🪂 持仓空投', '🟢 低门槛顺手福利', 'Hold RLUSD Share 600,000 XRP', 'https://www.binance.com/en/c?ref=evil', '🎁【交易所高性价比活动】\n📌 币安持有RLUSD瓜分60万XRP\n💰 收益/奖池：总池60万XRP｜无上限\n⏰ 活动时间：11-20 08:00 ~ 12-18 08:00 (北京时间)\n🧭 套保与防坑：每小时快照取日最低。\n🔗 原文：https://www.binance.com/en/c', ?)",
            (now_ts - 100,),
        )
        cp.commit()
        cp.close()

        ca = sqlite3.connect(d / "archive_14d.db")
        ca.execute("CREATE TABLE message_archive (id INT, ts INT, dt_bjt TEXT, level TEXT, category TEXT, msg TEXT)")
        # Duplicate Kraken CAD spam x2 + bogus XMR coord + mismatched Taiwan tag + conflicting ETF flash + real exchange announcement
        ca.execute(
            "INSERT INTO message_archive VALUES (1, ?, '2026-11-23 10:00:00', 'normal', '秒级快讯', ?)",
            (now_ts - 7200, "⚡【重要快讯 · 82分】 N1·X/@wublockchain12\n📌 Kraken确认加元交易对遭遇异常抛售，未发现系统入侵。\n🔴 利空 · 安全抛压\n🎯 先手坐标：XMR隐私币 $475.3"),
        )
        ca.execute(
            "INSERT INTO message_archive VALUES (2, ?, '2026-11-23 10:05:00', 'normal', '秒级快讯', ?)",
            (now_ts - 6900, "⚡【重要快讯 · 83分】 英文快讯雷达\n📌 Kraken加元交易对遭遇重抛，比特币报价一度低至2.7万美元，未发现入侵。\n🔴 利空 · 局部盘口异常"),
        )
        ca.execute(
            "INSERT INTO message_archive VALUES (3, ?, '2026-11-23 10:10:00', 'normal', '秒级快讯', ?)",
            (now_ts - 6600, "⚡【重要快讯 · 91分】 X快讯·ZeroHedge\n📌 台湾地区全面禁空、冻结股票出借，并推出150亿美元救市基金。\n🟢 偏多 · 被盗资金追回/冻结进展"),
        )
        ca.execute(
            "INSERT INTO message_archive VALUES (4, ?, '2026-11-23 10:15:00', 'normal', '秒级快讯', ?)",
            (now_ts - 6300, "🐋【链上巨鲸 · 83分】 英文快讯雷达\n📌 11月20日美股交易时段，美国比特币现货ETF净流出6.69亿美元。\n🔴 利空"),
        )
        ca.execute(
            "INSERT INTO message_archive VALUES (5, ?, '2026-11-20 20:01:41', 'major', '交易所公告', ?)",
            (now_ts - 200000, "🚨【交易所重大公告】 欧易 OKX\n📌 欧易关于下线部分现货交易对的公告\n🟡 中性/方向待定"),
        )
        ca.commit()
        ca.close()

        co = sqlite3.connect(d / "opportunity.db")
        if include_wallet:
            co.execute("CREATE TABLE wallet_halt_state (ex TEXT, coin TEXT, dep_ok INT, wd_ok INT, halted_since INT, updated_at INT)")
        co.execute("CREATE TABLE bn_oi_latest (symbol TEXT, base TEXT, price REAL, oi_usd REAL, vol_24h REAL, chg_1h_oi REAL, chg_1h_px REAL, funding_rate REAL, updated_at INT)")
        co.execute("INSERT INTO bn_oi_latest VALUES ('ACEUSDT', 'ACE', 0.072, 5400000, 202000000, 0.14, 5.2, -0.018, ?)", (now_ts,))
        co.execute("INSERT INTO bn_oi_latest VALUES ('BTCUSDT', 'BTC', 61500, 5600000000, 13000000000, 0.2, -0.3, 0.000045, ?)", (now_ts,))
        co.commit()
        co.close()

    def test_p0_fixes_macro_dca_promos_feed_and_oi_funding(self) -> None:
        now_dt = datetime(2026, 11, 23, 15, 30, tzinfo=BJT)
        now_ts = int(now_dt.timestamp())
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            self._seed_dbs(data_dir, include_wallet=False, dca_days=95)

            ov = exp.export_overview(data_dir, now_sec=now_ts)
            # 1. Stablecoin scope clarified
            self.assertEqual(ov["stablecoins"]["scope"], "DeFiLlama 全口径稳定币")
            st_item = next(x for x in ov["bottom_dashboard"]["items"] if x["key"] == "stables")
            self.assertIn("USDT + USDC", st_item["label"])
            # 2. Macro quotes reflect REAL regular close change & drawdown (NO fake 0.00%!)
            # At 15:30 BJT (02:30 ET) US market is CLOSED; at 18:30 BJT (05:30 ET) PRE_MKT is PRE
            nvda = next(x for x in ov["macro_quotes"] if x["symbol"] == "NVDA")
            self.assertEqual(nvda["price"], 125.53)
            self.assertEqual(nvda["chg_pct"], -14.0)
            self.assertEqual(nvda["session"], "CLOSED")
            self.assertEqual(nvda["dd_pct"], -48.42)
            pre_ts = int(datetime(2026, 11, 23, 18, 30, tzinfo=BJT).timestamp())
            self.assertEqual(exp._us_equity_session(pre_ts, "PRE_MKT"), ("PRE", True))
            # 3. TGA exported
            self.assertEqual(ov["tga"]["bal_b"], 891.2)

            # 4. DCA 3rd strategy MAFG restored & day 95 is latest
            labs = exp.export_labs(data_dir, now_dt=now_dt)
            expected_last = (date(2026, 8, 1) + timedelta(days=94)).isoformat()
            self.assertEqual(labs["dca_simulation"]["latest_date"], expected_last)
            self.assertEqual(len(labs["dca_simulation"]["history"]), 90)
            self.assertIn("MAFG", labs["dca_simulation"]["strategies"])
            self.assertEqual(labs["dca_simulation"]["strategies"]["MAFG"]["ret_pct"], 6.67)

            # 5. Promos filter out 2025 & past-deadline campaigns, clean URLs & structured fields
            promos = labs["exchange_promos"]
            self.assertEqual(len(promos), 1)
            self.assertEqual(promos[0]["exchange"], "Binance")
            self.assertEqual(promos[0]["url"], "https://www.binance.com/en/c")
            self.assertIn("60万XRP", promos[0]["reward"])
            self.assertNotIn("http", promos[0]["summary"])

            # 6. Feed hygiene: deduplicates Kraken, fixes Taiwan tag, drops bogus XMR & secondhand ETF, keeps Exchange Announcement
            feed = exp.export_feed(data_dir, now_sec=now_ts)
            items = feed["items"]
            self.assertEqual(len(items), 3)  # 1 Kraken + 1 Taiwan + 1 OKX announcement
            cats = {it["category"] for it in items}
            self.assertIn("交易所公告", cats)
            tw = next(it for it in items if "台湾" in it["headline"])
            self.assertNotIn("被盗", tw["impact_label"])
            kr = next(it for it in items if "Kraken" in it["headline"])
            self.assertNotIn("XMR", kr["market_ref"])
            self.assertNotIn("N1·", kr["source"])

            # 7. Binance OI + Extreme Funding ranks ACE (-1.80%/8h) #1 as danger!
            flows = exp.export_flows(data_dir, now_sec=now_ts)
            oi = flows["bn_oi_radar"]
            self.assertEqual(oi[0]["base"], "ACE")
            self.assertEqual(oi[0]["tone"], "danger")
            self.assertEqual(flows["etf_flows"]["summary"]["BTC"]["streak"], {"dir": "outflow", "days": 3})

            # 8. Desk signals & regime synthesized cleanly
            bundle = exp.build_bundle(data_dir)
            self.assertIsNotNone(bundle["overview"]["market_regime"])
            self.assertGreaterEqual(len(bundle["overview"]["desk_signals"]), 2)
            # Wallet DB missing wallet_halt_state -> fail-closed
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

    def test_staging_cache_busting_static_target_validation_and_tar(self) -> None:
        # Reject unsafe SSH hosts / dirs
        for bad in ("-oProxyCommand=id:/srv/site", "host:relative/path", "host:/etc", "host:/srv/../etc", "no_colon"):
            with self.assertRaises(ValueError, msg=f"Should reject {bad}"):
                syncer.parse_static_ssh_target(bad)
        self.assertEqual(syncer.parse_static_ssh_target("demo-host:/srv/danclub/"), ("demo-host", "/srv/danclub"))

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            self._seed_dbs(data_dir, include_wallet=False, dca_days=3)
            bundle = syncer._clean_bundle(exp.build_bundle(data_dir), blocklist_path=Path("/nonexistent"))
            api_bytes = syncer.build_verified_api_bytes(bundle, blocklist_path=Path("/nonexistent"))
            release = syncer.build_verified_release_bundle(ROOT / "site", api_bytes)

            # Verify automatic content-hash ?v= injected into staged index.html!
            html = release["index.html"].decode("utf-8")
            self.assertRegex(html, r"\./assets/style\.css\?v=[0-9a-f]{10}")
            self.assertRegex(html, r"\./assets/app\.js\?v=[0-9a-f]{10}")
            self.assertRegex(html, r"\./assets/war_arena\.js\?v=[0-9a-f]{10}")

            # Verify tar contains ONLY expected regular 0644 files
            tar_raw = syncer.build_static_tar_bytes(release)
            with tarfile.open(fileobj=io.BytesIO(tar_raw), mode="r:gz") as tf:
                names = sorted(m.name for m in tf.getmembers())
                self.assertEqual(names, sorted(release.keys()))
                for m in tf.getmembers():
                    self.assertTrue(m.isreg())
                    self.assertEqual(m.mode, 0o644)

            digests = syncer.publish_gh_pages(
                ROOT / "site",
                api_bytes,
                "git@github.com:danclub-open/danclub-terminal.git",
                dry_run_stage_only=True,
            )
            self.assertEqual(len(digests), 7)
            for name, raw in api_bytes.items():
                self.assertEqual(digests[name], hashlib.sha256(raw).hexdigest())

    def test_whale_exporter_masks_addresses_filters_bad_labels_and_computes_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wdb = Path(td) / "whale_intel.db"
            c = sqlite3.connect(wdb)
            c.execute("CREATE TABLE address_registry (identifier TEXT, entity_cluster_id TEXT, entity_label TEXT)")
            c.execute("CREATE TABLE onchain_profiles (identifier TEXT, hl_found INT, hl_display_name TEXT, hl_alltime_pnl REAL, hl_account_value REAL)")
            c.execute("CREATE TABLE live_ls_snapshots (ts INT, cohort TEXT, coin TEXT, long_entities INT, short_entities INT, balanced_entities INT, headcount_long_pct REAL, long_usd REAL, short_usd REAL, net_usd REAL, notional_long_pct REAL, btc_price REAL, eth_price REAL)")
            c.execute("CREATE TABLE live_entity_pos_history (ts INT, entity_cluster_id TEXT, tier TEXT, coin TEXT, szi REAL, signed_ntl_usd REAL, mark_px REAL)")
            c.execute("INSERT INTO address_registry VALUES ('0x1234567890abcdef1234567890abcdef1234abcd', 'ent_s1', 'Strategy / MicroStrategy')")
            c.execute("INSERT INTO address_registry VALUES ('0xabcdef1234567890abcdef1234567890abcdef9999', 'ent_c1', '厉害的，随着今天原油大涨，这个巨鲸抄底')")
            c.execute("INSERT INTO onchain_profiles VALUES ('0x1234567890abcdef1234567890abcdef1234abcd', 1, 'AlphaDesk', 50000000.0, 20000000.0)")
            c.execute("INSERT INTO onchain_profiles VALUES ('0xabcdef1234567890abcdef1234567890abcdef9999', 1, '', -20000000.0, 8000000.0)")
            c.execute("INSERT INTO live_ls_snapshots VALUES (1795419000, 'SMART_SA', 'BTC', 2, 8, 0, 20.0, 100000000, 300000000, -200000000, 25.0, 61500, 1725)")
            c.execute("INSERT INTO live_ls_snapshots VALUES (1795419000, 'COUNTER_C', 'BTC', 8, 2, 0, 80.0, 200000000, 40000000, 160000000, 83.3, 61500, 1725)")
            c.execute("INSERT INTO live_entity_pos_history VALUES (1795419000, 'ent_s1', 'S_SMART_WHALE', 'BTC', -1000, -61500000, 61500)")
            c.execute("INSERT INTO live_entity_pos_history VALUES (1795419000, 'ent_c1', 'C_COUNTER_WHALE', 'BTC', 500, 30750000, 61500)")
            c.commit()
            c.close()

            w = wexp.export_whales(wdb)
            self.assertTrue(w["available"])
            self.assertEqual(w["cohorts"]["BTC"]["divergence"]["spread_pct"], -58.3)
            self.assertEqual(w["cohorts"]["BTC"]["divergence"]["tone"], "danger")
            fls = {f["short_addr"]: f for f in w["flagships"]}
            self.assertEqual(fls["0x1234…abcd"]["display"], "AlphaDesk")
            self.assertEqual(fls["0x1234…abcd"]["ship_class"], "CARRIER")
            # Noisy tweet label MUST be stripped and fall back to masked short address!
            self.assertEqual(fls["0xabcd…9999"]["display"], "0xabcd…9999")
            self.assertEqual(fls["0xabcd…9999"]["ship_class"], "CRUISER")

    def test_redteam_9_p1_regressions_all_closed(self) -> None:
        # P1 #2: Thanksgiving 2026-11-26 10:00 ET, Good Friday 2026-04-03, empty/unparseable status -> CLOSED
        tg_ts = int(datetime(2026, 11, 26, 10, 0, tzinfo=exp.US_ET).timestamp())
        self.assertEqual(exp._us_equity_session(tg_ts, ""), ("CLOSED", True))
        self.assertEqual(exp._us_equity_session(tg_ts, "REG_MKT"), ("CLOSED", True))
        gf_ts = int(datetime(2026, 4, 3, 11, 0, tzinfo=exp.US_ET).timestamp())
        self.assertEqual(exp._us_equity_session(gf_ts, "REG_MKT"), ("CLOSED", True))
        norm_ts = int(datetime(2026, 11, 23, 11, 0, tzinfo=exp.US_ET).timestamp())
        self.assertEqual(exp._us_equity_session(norm_ts, ""), ("CLOSED", True))  # empty status NEVER LIVE
        self.assertEqual(exp._us_equity_session(norm_ts, "REG_MKT", "11/20/2026"), ("CLOSED", True))  # stale reg_date
        self.assertEqual(exp._us_equity_session(norm_ts, "REG_MKT", "garbage-date"), ("CLOSED", True))  # unparseable fail-closed
        self.assertEqual(exp._us_equity_session(norm_ts, "REG_MKT", "11/23/2026"), ("LIVE", False))
        sat_ts = int(datetime(2026, 11, 21, 12, 0, tzinfo=exp.US_ET).timestamp())
        self.assertFalse(exp._is_fx_metals_open(sat_ts))

        # P1 #3 & #2 fallback: quotes_60m fallback NEVER uses arbitrary oldest row & hides .VIX closed 0.0
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            c = sqlite3.connect(d / "macro_market.db")
            c.execute("CREATE TABLE dashboard_state (item_key TEXT, status TEXT, detail TEXT, updated_at INT)")
            c.execute("CREATE TABLE kv_state (k TEXT, v TEXT)")
            c.execute("CREATE TABLE stock_reg_cache (sym TEXT, reg_chg REAL, updated_at INT)")
            c.execute("INSERT INTO stock_reg_cache VALUES ('.VIX', 0.0, ?)", (sat_ts,))
            c.execute("CREATE TABLE quotes_60m (symbol TEXT, ts_sec INT, price REAL, oi_usd REAL)")
            c.execute("INSERT INTO quotes_60m VALUES ('XAU=', ?, 80.0, NULL)", (sat_ts - 5 * 86400,))
            c.execute("INSERT INTO quotes_60m VALUES ('XAU=', ?, 101.0, NULL)", (sat_ts - 3600,))
            c.execute("INSERT INTO quotes_60m VALUES ('.VIX', ?, 35.89, NULL)", (sat_ts - 3600,))
            c.execute("CREATE TABLE us10y_history (date_bjt TEXT, yield_pct REAL)")
            c.commit()
            c.close()
            ov = exp.export_overview(d, now_sec=sat_ts)
            xau = next(x for x in ov["macro_quotes"] if x["symbol"] == "XAU=")
            vix = next(x for x in ov["macro_quotes"] if x["symbol"] == ".VIX")
            self.assertEqual(xau["session"], "CLOSED")
            self.assertIsNone(xau["chg_pct"])  # No 24h benchmark -> None (NOT +26.25%!)
            self.assertEqual(vix["session"], "CLOSED")
            self.assertIsNone(vix["chg_pct"])  # Closed .VIX 0.0 -> None

        # P1 #4: ETF streak breaks on zero or None day
        self.assertEqual(
            exp._compute_etf_streak([{"total_musd": 10.0}, {"total_musd": 0.0}, {"total_musd": 10.0}]),
            {"dir": "inflow", "days": 1},
        )
        self.assertIsNone(exp._compute_etf_streak([{"total_musd": 10.0}, {"total_musd": 0.0}]))
        self.assertIsNone(exp._compute_etf_streak([{"total_musd": 10.0}, {"total_musd": None}]))

        # P1 #8: 95% vs 55% divergence MUST NOT call 55% "偏空"
        d95 = wexp.classify_divergence(95.0, 55.0)
        self.assertNotIn("偏空", d95["label"])
        d55 = wexp.classify_divergence(55.0, 95.0)
        self.assertNotIn("偏空", d55["label"])
        d_opp = wexp.classify_divergence(25.0, 82.5)
        self.assertIn("聪明钱偏空 / 反指偏多", d_opp["label"])

        # P1 #1, #6, #7, #9: Whale exporter privacy, full-position net, >32 hist 24h baseline, constant-price delta & coverage
        with tempfile.TemporaryDirectory() as td:
            wdb = Path(td) / "whale_intel.db"
            c = sqlite3.connect(wdb)
            c.execute("CREATE TABLE address_registry (identifier TEXT, entity_cluster_id TEXT, entity_label TEXT)")
            c.execute("CREATE TABLE onchain_profiles (identifier TEXT, hl_found INT, hl_display_name TEXT, hl_alltime_pnl REAL, hl_account_value REAL)")
            c.execute("CREATE TABLE live_ls_snapshots (ts INT, cohort TEXT, coin TEXT, long_entities INT, short_entities INT, balanced_entities INT, headcount_long_pct REAL, long_usd REAL, short_usd REAL, net_usd REAL, notional_long_pct REAL, btc_price REAL, eth_price REAL)")
            c.execute("CREATE TABLE live_entity_pos_history (ts INT, entity_cluster_id TEXT, tier TEXT, coin TEXT, szi REAL, signed_ntl_usd REAL, mark_px REAL)")
            # Entity 1: internal label '青桐七组' with NO hl_display_name & missing profile -> masked addr + None PnL
            # Also has +$1M BTC + 40 x -$40K alts = true net -$600K UNCHANGED across t0 and t1 -> action MUST be '持仓', NOT '反手'!
            t0 = 1795300000
            t_now = t0 + 86400
            c.execute("INSERT INTO address_registry VALUES ('0x111122223333444455556666777788889999aaaa', 'ent_p1', '青桐七组')")
            for ts_v in (t0, t_now):
                c.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_p1', 'S_SMART_WHALE', 'BTC', 10.0, 1000000.0, 100000.0)", (ts_v,))
                for k in range(40):
                    c.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_p1', 'S_SMART_WHALE', ?, -400.0, -40000.0, 100.0)", (ts_v, f"ALT{k}"))

            # Entity 2: 50 snapshots (>32!) where 24h ago was +$3M BTC, and last 35 snapshots are -$2M BTC -> MUST detect '反手'
            c.execute("INSERT INTO address_registry VALUES ('0x22223333444455556666777788889999aaaabbbb', 'ent_p2', 'internal_secret_tag')")
            c.execute("INSERT INTO onchain_profiles VALUES ('0x22223333444455556666777788889999aaaabbbb', 1, 'PublicHero', 12000000.0, 5000000.0)")
            c.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_p2', 'S_SMART_WHALE', 'BTC', 30.0, 3000000.0, 100000.0)", (t0,))
            for step in range(1, 50):
                c.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_p2', 'S_SMART_WHALE', 'BTC', -20.0, -2000000.0, 100000.0)", (t0 + step * 900,))
            c.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_p2', 'S_SMART_WHALE', 'BTC', -20.0, -2000000.0, 100000.0)", (t_now,))

            # Entity 3: pure price rise (+10%) with UNCHANGED szi=100 -> MUST report delta_usd=0 & '持仓', NOT '加仓'!
            c.execute("INSERT INTO address_registry VALUES ('0x3333444455556666777788889999aaaabbbbcccc', 'ent_p3', '')")
            c.execute("INSERT INTO address_registry VALUES ('0x444455556666777788889999aaaabbbbccccdddd', 'ent_p3', '')")
            # Partial profile: only 1 of 2 cluster addresses has profile -> alltime_pnl MUST be None!
            c.execute("INSERT INTO onchain_profiles VALUES ('0x3333444455556666777788889999aaaabbbbcccc', 1, '', 5000000.0, 2000000.0)")
            c.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_p3', 'C_COUNTER_WHALE', 'BTC', 100.0, 10000000.0, 100000.0)", (t0,))
            c.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_p3', 'C_COUNTER_WHALE', 'BTC', 100.0, 11000000.0, 110000.0)", (t_now,))
            c.commit()
            c.close()

            w = wexp.export_whales(wdb)
            by_addr = {f["short_addr"]: f for f in w["flagships"]}
            e1 = by_addr["0x1111…aaaa"]
            self.assertEqual(e1["display"], "0x1111…aaaa")  # internal label '青桐七组' NEVER leaked
            self.assertNotIn("青桐", json.dumps(w, ensure_ascii=False))
            self.assertEqual(e1["net_usd"], -600000.0)      # full position net
            self.assertEqual(e1["action"], "持仓")           # NOT false '反手'!
            self.assertIsNone(e1["alltime_pnl"])            # missing profile -> None (NOT 0!)
            self.assertIsNone(e1["account_value"])

            e2 = by_addr["0x2222…bbbb"]
            self.assertEqual(e2["display"], "PublicHero")
            self.assertEqual(e2["action"], "反手")           # found 24h baseline BEFORE slicing 50 pts!
            self.assertEqual(e2["alltime_pnl"], 12000000.0)

            e3 = by_addr["0x3333…cccc"]
            self.assertEqual(e3["delta_usd"], 0.0)          # constant-price szi unchanged -> 0!
            self.assertEqual(e3["action"], "持仓")           # NOT false '加仓'!
            self.assertIsNone(e3["alltime_pnl"])            # 1/2 partial coverage -> None!

    def test_frontend_zero_svg_distortion_and_has_x_axis(self) -> None:
        js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
        war_js = (ROOT / "site" / "assets" / "war_arena.js").read_text(encoding="utf-8")
        self.assertNotIn('preserveAspectRatio="none"', js)
        self.assertIn("${xSvg}", js)
        self.assertIn("flowBuckets", war_js)
        self.assertNotIn("trades60s", war_js)


if __name__ == "__main__":
    unittest.main()
