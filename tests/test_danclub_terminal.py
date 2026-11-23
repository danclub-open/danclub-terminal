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
            self.assertIn("上新·公告", cats)
            self.assertIn("LISTING", {it["cat_code"] for it in items})
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

            # Verify automatic content-hash ?v= injected into staged index.html & war room is Tab #1
            html = release["index.html"].decode("utf-8")
            self.assertRegex(html, r"\./assets/style\.css\?v=[0-9a-f]{10}")
            self.assertRegex(html, r"\./assets/app\.js\?v=[0-9a-f]{10}")
            self.assertRegex(html, r"\./assets/war_arena\.js\?v=[0-9a-f]{10}")
            self.assertLess(html.index('data-tab="war"'), html.index('data-tab="overview"'))
            for audio_rel in (
                "assets/audio/valkyries.mp3",
                "assets/audio/1812_cannon.mp3",
                "assets/audio/william_tell.mp3",
                "assets/audio/toreador.mp3",
                "assets/audio/mars_war.mp3",
            ):
                self.assertIn(audio_rel, release)
                self.assertGreater(len(release[audio_rel]), 100_000)

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

    def test_promo_expiry_not_masked_by_earlier_numeric_tilde_range(self) -> None:
        now_dt = datetime(2026, 11, 23, 16, 0, tzinfo=exp.BJT)
        # Earlier "估值1.26~1.41万亿美元" must NOT mask "11-18 14:00 ~ 11-18 22:00"
        row_expired = {
            "exchange": "Binance",
            "title": "币安钱包Anthropic代币化股票认购",
            "worth_level": "重点关注",
            "promo_type": "认购",
            "summary_text": "收益/奖池：参考价70~78U | 估值1.26~1.41万亿美元\n活动时间：11-18 14:00 ~ 11-18 22:00 (北京时间)",
            "url": "https://www.binance.com/en/support/announcement/test-expired",
            "created_at": int(now_dt.timestamp()) - 3 * 86400,
        }
        self.assertIsNone(exp._parse_clean_promo(row_expired, now_dt))

        # Explicit past title date >10 days ago with no future window must be filtered out
        row_stale_title = {
            "exchange": "Binance",
            "title": "Binance Alpha Trading Competition: Trade LAB (2026-11-12)",
            "worth_level": "反撸",
            "promo_type": "刷量竞赛",
            "summary_text": "收益/奖池：约 $20.0万",
            "url": "https://www.binance.com/en/support/announcement/test-stale",
            "created_at": int(now_dt.timestamp()) - 6 * 86400,
        }
        self.assertIsNone(exp._parse_clean_promo(row_stale_title, now_dt))

        # Active future window MUST still pass
        row_active = {
            "exchange": "Binance",
            "title": "币安持有RLUSD瓜分60万XRP",
            "worth_level": "常规",
            "promo_type": "持仓瓜分",
            "summary_text": "收益/奖池：60万XRP | 估值1.2~1.5倍\n活动时间：11-20 08:00 ~ 12-18 08:00 (北京时间)",
            "url": "https://www.binance.com/en/support/announcement/test-active",
            "created_at": int(now_dt.timestamp()) - 2 * 86400,
        }
        parsed = exp._parse_clean_promo(row_active, now_dt)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["exchange"], "Binance")

    def test_bstocks_overlay_eqn_merge_and_whale_x_accounts(self) -> None:
        # 1. Binance Spot bStocks overlay promotes 24/7 quotes when US market closed; mixed batch does NOT append extras
        bundle = {
            "overview": {
                "macro_quotes": [
                    {"symbol": "NVDA", "name": "英伟达", "group": "算力存储", "price": 125.53, "chg_pct": -14.01, "session": "CLOSED", "is_closed": True},
                    {"symbol": "SPY", "name": "标普500", "group": "美股指数", "price": 689.81, "chg_pct": -3.64, "session": "CLOSED", "is_closed": True},
                ]
            },
            "feed": {"items": []},
            "labs": {"exchange_promos": []},
        }
        bstocks = {
            "NVDA": {"bstock_symbol": "NVDABUSDT", "price": 135.74, "chg_pct": 2.44, "vol_usd": 59_000_000},
            "SPY": {"bstock_symbol": "SPYBUSDT", "price": 709.35, "chg_pct": 1.07, "vol_usd": 610_000},
            "TSLA": {"bstock_symbol": "TSLABUSDT", "price": 362.20, "chg_pct": 1.65, "vol_usd": 3_440_000},
        }
        syncer.apply_bstocks_overlay(bundle, bstocks)
        nvda = next(q for q in bundle["overview"]["macro_quotes"] if q["symbol"] == "NVDA")
        tsla = next(q for q in bundle["overview"]["macro_quotes"] if q["symbol"] == "TSLA")
        self.assertEqual(nvda["session"], "BSTOCKS_247")
        self.assertEqual(nvda["price"], 135.74)
        self.assertEqual(nvda["reg_px"], 125.53)  # Official close preserved!
        self.assertEqual(tsla["price_source"], "BINANCE_BSTOCKS")

        # When ANY US equity is LIVE (mixed or regular session), extra bStocks MUST NOT be appended!
        live_bundle = {
            "overview": {
                "macro_quotes": [
                    {"symbol": "NVDA", "name": "英伟达", "group": "算力存储", "price": 136.0, "chg_pct": 2.1, "session": "LIVE", "is_closed": False},
                    {"symbol": "SPY", "name": "标普500", "group": "美股指数", "price": 689.81, "chg_pct": -3.64, "session": "CLOSED", "is_closed": True},
                ]
            },
            "feed": {"items": []},
            "labs": {"exchange_promos": []},
        }
        syncer.apply_bstocks_overlay(live_bundle, bstocks)
        self.assertEqual(len(live_bundle["overview"]["macro_quotes"]), 2)
        self.assertEqual(live_bundle["overview"]["macro_quotes"][0]["session"], "LIVE")

        # 2. EQN multi-category news & earn merge deduplicates cleanly
        eqn_feed = [
            {"id": 0, "ts": 1795420000, "dt_bjt": "2026-11-23 16:00:00", "level": "major", "cat_code": "MACRO", "category": "美股·宏观", "source": "方程式财经", "headline": "美股科技股盘后大涨", "impact_label": "", "impact_tone": "bullish", "market_ref": "", "body": "", "coins": [], "url": "https://eqn.news/monitor/1"},
            {"id": 0, "ts": 1795419900, "dt_bjt": "2026-11-23 15:58:00", "level": "normal", "cat_code": "EARN", "category": "理财·羊毛", "source": "EQN · 币安重要公告", "headline": "Binance Earn USDC 活期享 5% 年化", "impact_label": "", "impact_tone": "neutral", "market_ref": "", "body": "", "coins": [], "url": "https://eqn.news/monitor/2"},
        ]
        eqn_promos = [
            {"exchange": "Binance", "tier": "HIGH", "tier_label": "高息理财", "worth_level": "高息理财", "earn_cat": "HIGH_YIELD", "earn_cat_label": "高息理财", "promo_type": "理财提醒", "title": "Binance Earn USDC 活期享 5% 年化", "reward": "最高 5% 年化", "window": "11-23", "summary": "活期理财", "url": "https://eqn.news/monitor/2", "created_at": 1795419900},
        ]
        syncer.merge_eqn_into_bundle(bundle, eqn_feed, eqn_promos)
        self.assertEqual(len(bundle["feed"]["items"]), 2)
        self.assertEqual(len(bundle["labs"]["exchange_promos"]), 1)
        self.assertEqual(bundle["labs"]["exchange_promos"][0]["earn_cat"], "HIGH_YIELD")

        # 3. Verified public whale X handles exposed, exchange handles (@Bitget_zh) blocked, entry/liq populated
        with tempfile.TemporaryDirectory() as td:
            wdb = Path(td) / "whale_intel.db"
            c = sqlite3.connect(wdb)
            c.execute("CREATE TABLE address_registry (identifier TEXT, entity_cluster_id TEXT, entity_label TEXT, external_labels_json TEXT)")
            c.execute("CREATE TABLE onchain_profiles (identifier TEXT, hl_found INT, hl_display_name TEXT, hl_alltime_pnl REAL, hl_month_pnl REAL, hl_week_pnl REAL, hl_account_value REAL, hl_positions_json TEXT, hl_hip3_positions_json TEXT)")
            c.execute("CREATE TABLE live_ls_snapshots (ts INT, cohort TEXT, coin TEXT, long_entities INT, short_entities INT, balanced_entities INT, headcount_long_pct REAL, long_usd REAL, short_usd REAL, net_usd REAL, notional_long_pct REAL, btc_price REAL, eth_price REAL)")
            c.execute("CREATE TABLE live_entity_pos_history (ts INT, entity_cluster_id TEXT, tier TEXT, coin TEXT, szi REAL, signed_ntl_usd REAL, mark_px REAL)")
            pos_j = json.dumps([{"coin": "BTC", "side": "LONG", "szi": 800, "ntl_usd": 50000000, "entry_px": 70000, "liq_px": 45000, "upnl": -2000000, "lev": 15, "lev_type": "cross"}])
            c.execute("INSERT INTO address_registry VALUES ('0xaaaa111122223333444455556666777788889999', 'ent_x1', 'X:@loraclexyz', ?)", (json.dumps(["Loracle (@loraclexyz)"]),))
            c.execute("INSERT INTO onchain_profiles VALUES ('0xaaaa111122223333444455556666777788889999', 1, '', 9000000, 1200000, 300000, 15000000, ?, '[]')", (pos_j,))
            c.execute("INSERT INTO live_entity_pos_history VALUES (1795420000, 'ent_x1', 'S_SMART_WHALE', 'BTC', 800, 50000000, 62500)")
            # Private entity_label 'X:@private_handle' with empty external_labels MUST NEVER leak!
            c.execute("INSERT INTO address_registry VALUES ('0xbbbb111122223333444455556666777788889999', 'ent_x2', 'X:@private_handle', '[]')")
            # Partial missing entry_px/upnl across 2 cluster legs MUST return None (NOT half-price or partial sum!)
            leg1 = json.dumps([{"coin": "ETH", "side": "LONG", "szi": 5000, "ntl_usd": 10000000, "entry_px": 2000, "liq_px": 1500, "upnl": 500000, "lev": 10, "lev_type": "cross"}])
            leg2 = json.dumps([{"coin": "ETH", "side": "LONG", "szi": 5000, "ntl_usd": 10000000, "entry_px": None, "liq_px": 1800, "upnl": None, "lev": 20, "lev_type": "isolated"}])
            c.execute("INSERT INTO address_registry VALUES ('0xcccc111122223333444455556666777788889999', 'ent_x2', 'X:@Bitget_zh', '[]')")
            c.execute("INSERT INTO onchain_profiles VALUES ('0xbbbb111122223333444455556666777788889999', 1, '', 1000000, 100000, 50000, 5000000, ?, '[]')", (leg1,))
            c.execute("INSERT INTO onchain_profiles VALUES ('0xcccc111122223333444455556666777788889999', 1, '', 1000000, 100000, 50000, 5000000, ?, '[]')", (leg2,))
            c.execute("INSERT INTO live_entity_pos_history VALUES (1795420000, 'ent_x2', 'S_SMART_WHALE', 'ETH', 10000, 20000000, 2000)")
            c.commit()
            c.close()

            w = wexp.export_whales(wdb)
            by_a = {f["short_addr"]: f for f in w["flagships"]}
            f1 = by_a["0xaaaa…9999"]
            self.assertEqual(f1["display"], "Loracle")
            self.assertEqual(f1["x_handle"], "loraclexyz")
            self.assertEqual(f1["x_url"], "https://x.com/loraclexyz")
            self.assertEqual(f1["month_pnl"], 1200000.0)
            self.assertEqual(f1["positions"][0]["entry_px"], 70000.0)
            self.assertEqual(f1["positions"][0]["liq_px"], 45000.0)
            self.assertEqual(f1["positions"][0]["lev"], 15.0)

            f2 = by_a["0xbbbb…9999"]
            self.assertIsNone(f2["x_handle"])
            self.assertNotIn("private_handle", json.dumps(w))
            self.assertEqual(f2["x_url"], "")
            self.assertEqual(f2["display"], "0xbbbb…9999")
            self.assertIsNone(f2["positions"][0]["entry_px"])  # partial leg missing -> None (NOT 1000!)
            self.assertIsNone(f2["positions"][0]["upnl"])      # partial leg missing -> None!
            self.assertEqual(f2["positions"][0]["liq_px"], 1800.0)  # tightest LONG liq_px (1800 > 1500)
            self.assertEqual(f2["positions"][0]["lev_type"], "mixed")

    def test_frontend_zero_svg_distortion_and_has_x_axis(self) -> None:
        js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
        war_js = (ROOT / "site" / "assets" / "war_arena.js").read_text(encoding="utf-8")
        self.assertNotIn('preserveAspectRatio="none"', js)
        self.assertIn("${xSvg}", js)
        self.assertIn("flowBuckets", war_js)
        self.assertNotIn("trades60s", war_js)
        # Regressions from Codex Redteam: null localStorage MUST default to 0.75 (not Number(null)===0) & safe try/catch
        self.assertIn("function readSavedVolume()", war_js)
        self.assertIn("raw === null", war_js)
        self.assertIn("if (!war.soundOn || !war.running || document.hidden || war.pausedByHidden", war_js)
        self.assertIn("el.load()", war_js)
        self.assertIn("safeStoreGet", js)

    def test_buyside_audit_whale_reconciliation_feed_macro_and_promo_dedup(self) -> None:
        # 1. Macro vs Crypto vs INTEL classification (\bSEC\b must NOT match "Secretary")
        self.assertEqual(
            syncer._classify_eqn_item(["main"], "UK Secretary of State Haigh: Competition should thrive", "FinancialJuice"),
            ("MACRO", "美股·宏观"),
        )
        self.assertEqual(
            syncer._classify_eqn_item(["main"], "LIBYA NOC: SHARARA OIL PIPELINE REOPENS", "FirstSquawk"),
            ("MACRO", "美股·宏观"),
        )
        self.assertEqual(
            syncer._classify_eqn_item(["main"], "SEC approves spot ETH staking amendment", "Bloomberg"),
            ("INTEL", "深度预警"),
        )
        self.assertEqual(
            syncer._classify_eqn_item(["main"], "Binance lists new BTC perp contract", "FirstSquawk"),
            ("CRYPTO", "加密快讯"),
        )

        # 2. Cross-source promo semantic deduplication (glued Chinese+ASCII tokens) & clean sentence split
        k1 = syncer._promo_semantic_key("Binance", "币安持有RLUSD瓜分60万XRP", "", "HIGH_YIELD")
        k2 = syncer._promo_semantic_key("Binance", "在任意币安账户持有RLUSD，瓜分60万XRP奖励", "", "HIGH_YIELD")
        self.assertTrue(k1)
        self.assertEqual(k1, k2)
        # Single-token / VIP marketing promos and distinct query-id URLs MUST NOT collide
        self.assertEqual(syncer._promo_semantic_key("Binance", "VIP USDT 10% 活期活动A", "", "HIGH_YIELD"), "")
        self.assertEqual(syncer._promo_semantic_key("Binance", "VIP USDT 10% 活期活动B", "", "HIGH_YIELD"), "")
        self.assertNotEqual(
            syncer._norm_promo_url("https://example.com/promo?id=101&utm_source=x"),
            syncer._norm_promo_url("https://example.com/promo?id=102&utm_source=y"),
        )
        self.assertEqual(
            syncer._norm_promo_url("https://example.com/promo?id=101&utm_source=x"),
            syncer._norm_promo_url("https://example.com/promo?id=101&ref=abc"),
        )
        t, s = syncer._split_promo_title_summary(
            "STBL (STBL) 现已在币安 Alpha 上线！拥有至少 250 点币安 Alpha 积分的用户即可领取 1,675 枚 STBL 代币空投，先到先得。",
            "币安公告",
        )
        self.assertTrue(t.endswith("！"))
        self.assertTrue(s.startswith("拥有至少"))

        # 3. Multi-venue whale reconciliation + low-price meme precision + 5% unprofiled leg fail-closed
        with tempfile.TemporaryDirectory() as td:
            wdb = Path(td) / "whale_audit.db"
            c = sqlite3.connect(str(wdb))
            c.executescript(
                """
                CREATE TABLE address_registry (identifier TEXT, entity_cluster_id TEXT, entity_label TEXT, external_labels_json TEXT);
                CREATE TABLE onchain_profiles (
                    identifier TEXT, hl_found INT, hl_display_name TEXT, hl_alltime_pnl REAL,
                    hl_month_pnl REAL, hl_week_pnl REAL, hl_account_value REAL,
                    hl_positions_json TEXT, hl_hip3_positions_json TEXT, aster_positions_json TEXT
                );
                CREATE TABLE live_ls_snapshots (ts INT, cohort TEXT, coin TEXT, long_entities INT, short_entities INT, balanced_entities INT, headcount_long_pct REAL, long_usd REAL, short_usd REAL, net_usd REAL, notional_long_pct REAL, btc_price REAL, eth_price REAL);
                CREATE TABLE live_entity_pos_history (ts INT, entity_cluster_id TEXT, tier TEXT, coin TEXT, szi REAL, signed_ntl_usd REAL, mark_px REAL);
                """
            )
            # Whale 1: Cross-venue HL (-650) + Aster (-150) = -800 BTC exact match
            c.execute("INSERT INTO address_registry VALUES ('0x1111111111111111111111111111111111111111', 'ent_mv', 'priv', '[]')")
            c.execute(
                "INSERT INTO onchain_profiles VALUES (?, 1, 'MultiVenue', 1e6, 5e5, 1e5, 5e6, ?, '[]', ?)",
                (
                    "0x1111111111111111111111111111111111111111",
                    json.dumps([{"coin": "BTC", "side": "SHORT", "szi": -650.0, "ntl_usd": 39000000.0, "entry_px": 66000.0, "liq_px": 85000.0, "upnl": 3900000.0, "lev": 20, "lev_type": "cross"}]),
                    json.dumps([{"coin": "BTC", "side": "SHORT", "szi": -150.0, "ntl_usd": 9000000.0, "entry_px": 0.0, "liq_px": None, "upnl": 900000.0, "lev": None}]),
                ),
            )
            c.execute("INSERT INTO live_entity_pos_history VALUES (1795430000, 'ent_mv', 'S_SMART_WHALE', 'BTC', -800.0, -48000000.0, 60000.0)")
            # Whale 2: Unprofiled 5% extra leg (prof=95 vs live=100 -> must fail closed!)
            c.execute("INSERT INTO address_registry VALUES ('0x2222222222222222222222222222222222222222', 'ent_drift', 'priv', '[]')")
            c.execute(
                "INSERT INTO onchain_profiles VALUES (?, 1, 'Drifted', 1e6, 5e5, 1e5, 5e6, ?, '[]', '[]')",
                (
                    "0x2222222222222222222222222222222222222222",
                    json.dumps([{"coin": "BTC", "side": "LONG", "szi": 95.0, "ntl_usd": 9500000.0, "entry_px": 100.0, "liq_px": 50.0, "upnl": 47.5, "lev": 10, "lev_type": "cross"}]),
                ),
            )
            c.execute("INSERT INTO live_entity_pos_history VALUES (1795430000, 'ent_drift', 'S_SMART_WHALE', 'BTC', 100.0, 10050000.0, 100.5)")
            # Whale 3: Low-price meme token (1e9 @ 0.000060 -> mark 0.000100 => +40,000 exact uPnL, NOT 0!)
            c.execute("INSERT INTO address_registry VALUES ('0x3333333333333333333333333333333333333333', 'ent_meme', 'priv', '[]')")
            c.execute(
                "INSERT INTO onchain_profiles VALUES (?, 1, 'MemeWhale', 1e6, 5e5, 1e5, 5e6, ?, '[]', '[]')",
                (
                    "0x3333333333333333333333333333333333333333",
                    json.dumps([{"coin": "PEPE", "side": "LONG", "szi": 1000000000.0, "ntl_usd": 100000.0, "entry_px": 0.00006, "liq_px": 0.00003, "upnl": 40000.0, "lev": 5, "lev_type": "cross"}]),
                ),
            )
            c.execute("INSERT INTO live_entity_pos_history VALUES (1795430000, 'ent_meme', 'S_SMART_WHALE', 'PEPE', 1000000000.0, 100000.0, 0.0001)")
            c.commit()
            c.close()

            w = wexp.export_whales(wdb)
            by_d = {f["display"]: f for f in w["flagships"]}
            p_mv = by_d["MultiVenue"]["positions"][0]
            self.assertEqual(p_mv["venues"], ["Aster", "HL"])
            self.assertIsNone(p_mv["entry_px"])  # Aster lacks entry_px -> fail-closed
            self.assertIsNone(p_mv["liq_px"])    # Cross-venue -> omit HL-only liq_px
            self.assertEqual(p_mv["upnl"], 4800000.0)

            p_dr = by_d["Drifted"]["positions"][0]
            self.assertIsNone(p_dr["entry_px"])  # 5% qty drift -> fail-closed!
            self.assertIsNone(p_dr["upnl"])
            self.assertIsNone(p_dr["liq_px"])

            p_mm = by_d["MemeWhale"]["positions"][0]
            self.assertEqual(p_mm["entry_px"], 0.00006)
            self.assertEqual(p_mm["upnl"], 40000.0)

    def test_english_feed_translation_cache_and_alert_ui(self) -> None:
        # 1. Language detector: English-only & mostly-English -> True; Chinese with tickers -> False
        self.assertTrue(syncer._needs_zh_translation("Federal Reserve Chair says rate cuts remain data dependent"))
        self.assertTrue(syncer._needs_zh_translation("【美股】US Treasury Secretary announces fresh semiconductor tariff guidance"))
        self.assertFalse(syncer._needs_zh_translation("比特币 BTC 突破 10 万美元，ETF 单日净流入 5.2 亿美元"))
        self.assertFalse(syncer._needs_zh_translation("币安理财 Simple Earn 推出 USDT 活期高息活动最高享 18% APR"))
        self.assertFalse(syncer._needs_zh_translation("BTC +3%"))

        # 2. Validator rejects garbage / error banners / non-CJK output
        self.assertEqual(syncer._valid_zh_result("MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS"), "")
        self.assertEqual(syncer._valid_zh_result("Only English text returned"), "")
        self.assertEqual(syncer._valid_zh_result("美联储主席表示降息仍取决于经济数据"), "美联储主席表示降息仍取决于经济数据")

        # 3. Persistent cache, headline_en preservation, garbage rejection, and 2nd-run cache hit
        with tempfile.TemporaryDirectory() as td:
            cache_file = Path(td) / "zh_cache.json"
            calls: list[list[str]] = []

            def fake_translator(texts: list[str]) -> list[str]:
                calls.append(list(texts))
                mapping = {
                    "Federal Reserve holds interest rates steady amid sticky inflation": "美联储因通胀粘性维持利率不变",
                    "Binance launches zero-fee trading campaign for spot pairs": "币安推出多币种现货零手续费交易活动",
                    "Unknown wire returning error text": "MYMEMORY WARNING: QUOTA EXCEEDED",
                }
                return [mapping.get(t, "") for t in texts]

            def make_bundle() -> dict:
                return {
                    "feed": {
                        "items": [
                            {
                                "id": 101,
                                "ts": 1795430000,
                                "headline": "Federal Reserve holds interest rates steady amid sticky inflation",
                                "body": "",
                                "cat_code": "MACRO",
                            },
                            {
                                "id": 102,
                                "ts": 1795429900,
                                "headline": "比特币现货 ETF 连续三日净流入",
                                "body": "",
                                "cat_code": "CRYPTO",
                            },
                            {
                                "id": 103,
                                "ts": 1795429800,
                                "headline": "Unknown wire returning error text",
                                "body": "",
                                "cat_code": "CRYPTO",
                            },
                        ]
                    },
                    "labs": {
                        "exchange_promos": [
                            {"exchange": "Binance", "title": "Binance launches zero-fee trading campaign for spot pairs"}
                        ]
                    },
                }

            b1 = make_bundle()
            applied1 = syncer.translate_english_feed_items(b1, cache_path=cache_file, translator=fake_translator)
            self.assertEqual(applied1, 2)
            self.assertEqual(len(calls), 1)
            self.assertEqual(b1["feed"]["items"][0]["headline"], "美联储因通胀粘性维持利率不变")
            self.assertEqual(
                b1["feed"]["items"][0]["headline_en"],
                "Federal Reserve holds interest rates steady amid sticky inflation",
            )
            # Chinese item untouched, no headline_en added
            self.assertEqual(b1["feed"]["items"][1]["headline"], "比特币现货 ETF 连续三日净流入")
            self.assertNotIn("headline_en", b1["feed"]["items"][1])
            # Failed/bad translation leaves English intact rather than polluting with warning text
            self.assertEqual(b1["feed"]["items"][2]["headline"], "Unknown wire returning error text")
            self.assertEqual(b1["labs"]["exchange_promos"][0]["title"], "币安推出多币种现货零手续费交易活动")
            self.assertTrue(cache_file.exists())

            # Second run should hit disk cache for the 2 cached items and only ask translator for the uncached failed one
            b2 = make_bundle()
            applied2 = syncer.translate_english_feed_items(b2, cache_path=cache_file, translator=fake_translator)
            self.assertEqual(applied2, 2)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1], ["Unknown wire returning error text"])
            self.assertEqual(b2["feed"]["items"][0]["headline"], "美联储因通胀粘性维持利率不变")

            # 3b. Pre-egress privacy check: blocklisted / email text NEVER sent to external translator
            bl = Path(td) / "blocklist.txt"
            bl.write_text("TopSecretCodename\n", encoding="utf-8")
            bl.chmod(0o600)
            egress_seen: list[str] = []

            def spy_translator(texts: list[str]) -> list[str]:
                egress_seen.extend(texts)
                return ["译文正常通过校验" for _ in texts]

            b_priv = {
                "feed": {
                    "items": [
                        {"id": 201, "ts": 1795430000, "headline": "Project TopSecretCodename launches institutional vault today"},
                        {"id": 202, "ts": 1795430001, "headline": "Contact fake.trader@gmail.com for treasury wire details"},
                        {"id": 203, "ts": 1795430002, "headline": "Ethereum developers finalize next mainnet upgrade timeline"},
                    ]
                },
                "labs": {"exchange_promos": []},
            }
            syncer.translate_english_feed_items(
                b_priv,
                cache_path=Path(td) / "priv_cache.json",
                translator=spy_translator,
                blocklist_path=bl,
                require_blocklist=True,
            )
            self.assertEqual(egress_seen, ["Ethereum developers finalize next mainnet upgrade timeline"])

            # 3c. Over-length MyMemory fallback refuses silent truncation
            self.assertEqual(syncer._translate_single_mymemory("A" * 265), "")

        # 4. Frontend feed alert controls, timestamp guard & escaping sanity check
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
        for needle in ('id="nav-feed-badge"', 'id="feed-alert-mode"', 'id="feed-alert-scope"', 'id="btn-feed-test-sound"', 'id="btn-feed-show-en"'):
            self.assertIn(needle, html)
        for needle in (
            "playFeedChime",
            "speakFeedHeadlines",
            "pollFeedLive",
            "ingestFeedData",
            "validFeedTs",
            "parseSnapshotGenMs",
            "feedPrevSnapshotKeys",
            "esc(it.headline_en)",
        ):
            self.assertIn(needle, app_js)

    def test_v1_liquidation_exporter_whale_magnets_and_war_ui(self) -> None:
        now = 1795430000
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # 1. Seed mock CoinGlass liquidation.db with real-format events + ignored leveraged ETF + wick alert
            c = sqlite3.connect(str(d / "liquidation.db"))
            c.executescript(
                """
                CREATE TABLE liq_events (ts INT, exchange TEXT, coin TEXT, side TEXT, usd REAL, px REAL);
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, ts INT, alert_key TEXT, level TEXT, message TEXT);
                """
            )
            c.execute("INSERT INTO liq_events VALUES (?, 'CG:Hyperliquid', 'BTC', 'short', 1500000.0, 62000.0)", (now - 1200,))
            c.execute("INSERT INTO liq_events VALUES (?, 'CG:Binance', 'BTC', 'long', 500000.0, 61000.0)", (now - 7200,))
            c.execute("INSERT INTO liq_events VALUES (?, 'CG:OKX', 'ETH', 'short', 400000.0, 1750.0)", (now - 600,))
            c.execute("INSERT INTO liq_events VALUES (?, 'CG:Binance', 'SOXL', 'long', 999999.0, 30.0)", (now - 300,))
            # Future row (+120s) MUST NOT enter [now-86400, now] totals; 4050 extra small rows MUST NOT be truncated!
            c.execute("INSERT INTO liq_events VALUES (?, 'CG:Binance', 'BTC', 'long', 888888.0, 61000.0)", (now + 120,))
            c.executemany(
                "INSERT INTO liq_events VALUES (?, 'CG:Gate', 'SOL', 'long', 1000.0, 72.0)",
                [(now - 100 - (i % 1000),) for i in range(4050)],
            )
            c.execute(
                "INSERT INTO alerts VALUES (1, ?, 'wick_up:ETH', 'major', ?)",
                (now - 900, "📍💥【向上插针爆空】ETH\n• 针尖高点 $1,752 → 现价 $1,733\n• 爆空 1,999万美元（非投资建议）"),
            )
            c.commit()
            c.close()

            liq = exp.export_liquidations(d, now_sec=now)
            self.assertTrue(liq["available"])
            self.assertEqual(liq["exchanges_count"], 4)
            # SOXL leveraged ETF and future +120s row MUST be excluded; all 4050 SOL rows MUST be counted!
            self.assertEqual(liq["windows"]["24h"]["ALL"]["total_usd"], 2400000.0 + 4050000.0)
            self.assertEqual(liq["windows"]["24h"]["SOL"]["count"], 4050)
            self.assertEqual(liq["windows"]["24h"]["BTC"]["short_usd"], 1500000.0)
            self.assertEqual(liq["windows"]["24h"]["BTC"]["long_usd"], 500000.0)
            self.assertEqual(liq["windows"]["1h"]["BTC"]["short_usd"], 1500000.0)
            self.assertEqual(liq["windows"]["1h"]["BTC"]["long_usd"], 0.0)
            self.assertEqual(liq["windows"]["24h"]["BTC"]["top_exchanges"][0]["ex"], "HL")
            self.assertEqual(liq["windows"]["24h"]["BTC"]["top_exchanges"][0]["pct"], 75.0)
            # Detail list still strictly >= $25K only (excludes the 4050 $1K small events!)
            self.assertEqual(len(liq["recent_events"]), 3)
            # Hourly 24h rhythm buckets must have exactly 24 hours, exclude SOXL & future rows, and preserve exact totals
            self.assertEqual(len(liq["hourly_24h"]["ALL"]), 24)
            self.assertEqual(len(liq["hourly_24h"]["BTC"]), 24)
            self.assertEqual(
                sum(b["long_usd"] + b["short_usd"] for b in liq["hourly_24h"]["ALL"]),
                2400000.0 + 4050000.0,
            )
            self.assertEqual(liq["hourly_24h"]["BTC"][-1]["short_usd"], 1500000.0)
            self.assertEqual(len(liq["wick_alerts"]), 1)

            # 1b. _clean_bundle rejects corrupted or incomplete hourly_24h series cleanly without crashing
            raw_b = exp.build_bundle(d)
            raw_b["liquidations"]["hourly_24h"]["BTC"][5]["ts"] = "bad_ts"
            raw_b["liquidations"]["hourly_24h"]["ETH"] = raw_b["liquidations"]["hourly_24h"]["ETH"][:23]
            cleaned_b = syncer._clean_bundle(raw_b, blocklist_path=Path("/nonexistent"))
            cleaned_hr = cleaned_b["whales"]["liquidations"]["hourly_24h"]
            self.assertIn("ALL", cleaned_hr)
            self.assertNotIn("BTC", cleaned_hr)
            self.assertNotIn("ETH", cleaned_hr)
            self.assertEqual(liq["wick_alerts"][0]["kind"], "WICK_UP")
            self.assertNotIn("非投资建议", liq["wick_alerts"][0]["detail"])

            # 2. Seed whale_intel.db with single-venue Red-HP flagship + recent move
            wdb = d / "whale_v1.db"
            wc = sqlite3.connect(str(wdb))
            wc.executescript(
                """
                CREATE TABLE address_registry (identifier TEXT, entity_cluster_id TEXT, entity_label TEXT, external_labels_json TEXT);
                CREATE TABLE onchain_profiles (
                    identifier TEXT, hl_found INT, hl_display_name TEXT, hl_alltime_pnl REAL,
                    hl_month_pnl REAL, hl_week_pnl REAL, hl_account_value REAL,
                    hl_positions_json TEXT, hl_hip3_positions_json TEXT, aster_positions_json TEXT
                );
                CREATE TABLE live_ls_snapshots (ts INT, cohort TEXT, coin TEXT, long_entities INT, short_entities INT, balanced_entities INT, headcount_long_pct REAL, long_usd REAL, short_usd REAL, net_usd REAL, notional_long_pct REAL, btc_price REAL, eth_price REAL);
                CREATE TABLE live_entity_pos_history (ts INT, entity_cluster_id TEXT, tier TEXT, coin TEXT, szi REAL, signed_ntl_usd REAL, mark_px REAL);
                """
            )
            wc.execute("INSERT INTO address_registry VALUES ('0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'ent_red', 'priv', '[]')")
            wc.execute(
                "INSERT INTO onchain_profiles VALUES (?, 1, 'RedBoss', -5e6, -1e6, -5e5, 3e6, ?, '[]', '[]')",
                (
                    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    json.dumps([{"coin": "BTC", "side": "SHORT", "szi": -300.0, "ntl_usd": 18000000.0, "entry_px": 59000.0, "liq_px": 62100.0, "upnl": -300000.0, "lev": 40, "lev_type": "cross"}]),
                ),
            )
            wc.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_red', 'C_COUNTER_WHALE', 'BTC', -100.0, -6000000.0, 60000.0)", (now - 7200,))
            wc.execute("INSERT INTO live_entity_pos_history VALUES (?, 'ent_red', 'C_COUNTER_WHALE', 'BTC', -300.0, -18000000.0, 60000.0)", (now,))
            wc.commit()
            wc.close()

            w = wexp.export_whales(wdb)
            btc_mag = w["liq_magnets"]["BTC"]
            self.assertEqual(btc_mag["short_wall"]["nearest_px"], 62100.0)
            self.assertAlmostEqual(btc_mag["short_wall"]["nearest_dist_pct"], 3.5, places=1)
            self.assertEqual(btc_mag["short_wall"]["within_10pct_usd"], 18000000.0)
            self.assertEqual(len(w["recent_moves"]), 1)
            self.assertEqual(w["recent_moves"][0]["action"], "加仓")

        # 3. Verify V1 UI elements & strict ctVal guard in war_arena.js
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        war_js = (ROOT / "site" / "assets" / "war_arena.js").read_text(encoding="utf-8")
        for needle in ('id="war-liq-magnet"', 'id="war-mag-crowd"', 'id="liq-rhythm-bars"', 'id="btn-war-liq-beep"', 'id="war-rail-tabs"', 'id="war-rail-liq"', 'id="war-liq-feed"', 'id="liq-scope-group"'):
            self.assertIn(needle, html)
        for needle in (
            "ensureOkxCtValMap",
            "connectOkxLiqSocket",
            "connectBnLiqSocket",
            "bnLiqReconnectTimer",
            "liqWsOkxSubscribed",
            "getMatchedLivePx",
            "scheduleLiqRailRender",
            "usedLiveIdx",
            "renderLiqMagnet",
            "renderLiqRail",
            "renderLiqRhythm",
            "renderLeverageCrowding",
            "playLiqRadarBeep",
            "liveWhaleDistPct",
        ):
            self.assertIn(needle, war_js)


if __name__ == "__main__":
    unittest.main()
