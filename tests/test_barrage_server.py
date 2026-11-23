import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from scripts import barrage_server as bs


class TestBarrageStoreAndHttp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.db_path = self.tmp_dir / "barrage.db"
        self.secret_path = self.tmp_dir / "hmac.secret"
        self.secret = bs.load_or_create_secret(self.secret_path)
        self.store = bs.BarrageStore(self.db_path, self.secret)
        self.server = bs.create_server("127.0.0.1", 0, self.store)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=3)
        self.store.close()
        self._tmp.cleanup()

    def _req(self, method: str, path: str, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = dict(headers or {})
        raw = None
        if body is not None:
            raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
            hdrs["Content-Length"] = str(len(raw))
        conn.request(method, path, body=raw, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        resp_hdrs = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        parsed = json.loads(data.decode("utf-8")) if data else None
        return resp.status, resp_hdrs, parsed

    def test_binary_hmac_secret_preserved_verbatim(self) -> None:
        # P1 #5: Leading/trailing whitespace bytes in random 32-byte key must NOT be stripped on reload
        p = self.tmp_dir / "whitespace_key.secret"
        key_with_ws = b"\n\t " + (b"X" * 26) + b" \r\n"
        self.assertEqual(len(key_with_ws), 32)
        p.write_bytes(key_with_ws)
        loaded = bs.load_or_create_secret(p)
        self.assertEqual(loaded, key_with_ws)

    def test_sanitize_spam_and_surrogates(self) -> None:
        self.assertEqual(bs.sanitize_text("  多军\x00\u202e冲锋\n\t必胜  "), "多军冲锋 必胜")
        self.assertIsNone(bs.check_spam("BTC猎手", "95,000 压力位到了，多军注意止盈！20x 别满仓"))
        self.assertIsNone(bs.check_spam("匿名战友", "ETH/BTC 汇率反弹，看看聪明钱动向"))
        self.assertIsNotNone(bs.check_spam("玩家", "来这里 https://evil.example 领空投"))
        self.assertIsNotNone(bs.check_spam("玩家", "加ｖ信 12345 带单稳赚"))
        self.assertIsNotNone(bs.check_spam("玩家", "联系 13812345678 进VIP"))
        self.assertIsNotNone(bs.check_spam("玩家", "哈哈哈哈哈哈哈哈哈哈哈哈"))
        self.assertIsNotNone(bs.check_spam("t.me/scam", "冲冲冲"))

        # Unpaired UTF-16 surrogate rejected cleanly with 400
        st_sur, _ = self.store.post_message(
            client_ip="198.51.100.8", nick="A", side="LONG", coin="BTC", text="bad\ud800text"
        )
        self.assertEqual(st_sur, 400)

    def test_cursor_pagination_and_window_moderation_sync(self) -> None:
        # Regression 1 & 2: Interleaved posts, multi-page catch-up (after_id=0 + has_more), likes & hide sync
        base_ts = 1_800_000_000
        ids = []
        for i in range(1, 8):
            st, res = self.store.post_message(
                client_ip=f"203.0.113.{i}",
                nick=f"战友{i}",
                side="LONG" if i % 2 else "SHORT",
                coin="BTC",
                text=f"第 {i} 波攻势",
                now_ts=base_ts + i,
            )
            self.assertEqual(st, 201)
            ids.append(res["item"]["id"])

        # Explicit after_id=0 catches up in ASC order with limit=3 across multiple pages
        collected = []
        cursor = 0
        for _ in range(5):
            st, _, page = self._req("GET", f"/api/v1/barrage?after_id={cursor}&limit=3&include_window=1")
            self.assertEqual(st, 200)
            collected.extend(x["id"] for x in page["items"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        self.assertEqual(collected, ids)
        self.assertEqual(cursor, ids[-1])

        # Like item #3 and hide item #5; authoritative window reflects BOTH immediately
        st_l, _, l_res = self._req(
            "POST",
            "/api/v1/barrage/like",
            {"id": ids[2]},
            headers={"X-Real-Client-IP": "203.0.113.99", "Origin": "https://danclub.duckdns.org"},
        )
        self.assertEqual(st_l, 200)
        self.assertEqual(l_res["likes"], 1)
        self.assertTrue(self.store.hide_message(ids[4]))

        st_sync, _, sync_page = self._req("GET", f"/api/v1/barrage?after_id={cursor}&limit=30&include_window=1")
        self.assertEqual(st_sync, 200)
        self.assertEqual(sync_page["items"], [])
        win_map = {x["id"]: x for x in sync_page["window"]}
        self.assertNotIn(ids[4], win_map)  # Hidden message immediately evicted
        self.assertEqual(win_map[ids[2]]["likes"], 1)  # Updated like count synced

    def test_high_volume_2050_preserves_24h_stats_and_1h_rate_limit(self) -> None:
        # Regression 3 (P1 #4): >2050 messages within 24h must NOT truncate 24h stats or 1h IP rate limits
        base_ts = 1_810_000_000
        conn = sqlite3.connect(str(self.db_path))
        rows = []
        # Seed 2020 messages inside the last 24h (1010 LONG, 1010 SHORT) from diverse IPs
        for i in range(2020):
            side = "LONG" if i % 2 == 0 else "SHORT"
            ip_h = self.store.hash_ip(f"10.0.{i // 250}.{i % 250}")
            rows.append((base_ts - 7200 + i, "BTC", side, "大军", f"批次消息 {i}", f"批次消息 {i}", ip_h, 0, 0))
        # Seed 39 messages in the last 30 minutes from one tracked IP
        target_ip = "198.51.100.77"
        target_hash = self.store.hash_ip(target_ip)
        for j in range(39):
            rows.append((base_ts - 1800 + j * 10, "BTC", "LONG", "活跃者", f"老消息 {j}", f"老消息 {j}", target_hash, 0, 0))
        conn.executemany(
            "INSERT INTO barrage_messages (created_at, coin, side, nick, text, norm_text, ip_hash, likes, hidden) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()

        # Force prune trigger on next insert
        self.store._insert_counter = 49
        st_ok, res_ok = self.store.post_message(
            client_ip=target_ip,
            nick="活跃者",
            side="LONG",
            coin="BTC",
            text="第40条踩线发言",
            now_ts=base_ts,
        )
        self.assertEqual(st_ok, 201)
        # All 2020 + 39 + 1 = 2060 messages in last 24h must survive prune intact!
        self.assertEqual(res_ok["stats_24h"]["total"], 2060)
        self.assertEqual(res_ok["stats_24h"]["long"], 1050)
        self.assertEqual(res_ok["stats_24h"]["short"], 1010)

        # 41st post within 1h from target_ip must still be blocked by 1h limit (40)
        st_blk, _ = self.store.post_message(
            client_ip=target_ip,
            nick="活跃者",
            side="LONG",
            coin="BTC",
            text="第41条必须拦截",
            now_ts=base_ts + 10,
        )
        self.assertEqual(st_blk, 429)

    def test_origin_content_type_strict_int_and_privacy(self) -> None:
        # Disallowed third-party browser Origin blocked on OPTIONS & POST
        st_opt_bad, _, _ = self._req("OPTIONS", "/api/v1/barrage", headers={"Origin": "https://evil.example"})
        self.assertEqual(st_opt_bad, 403)
        st_post_bad, _, _ = self._req(
            "POST",
            "/api/v1/barrage",
            {"nick": "X", "side": "LONG", "coin": "BTC", "text": "跨站攻击"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(st_post_bad, 403)

        # Allowed GitHub Pages Origin succeeds and echoes Vary: Origin
        st_opt_ok, hdrs_opt, _ = self._req(
            "OPTIONS", "/api/v1/barrage", headers={"Origin": "https://danclub-open.github.io"}
        )
        self.assertEqual(st_opt_ok, 204)
        self.assertEqual(hdrs_opt.get("access-control-allow-origin"), "https://danclub-open.github.io")
        self.assertEqual(hdrs_opt.get("vary"), "Origin")

        # Non-JSON Content-Type rejected with 415
        st_ct, _, _ = self._req(
            "POST",
            "/api/v1/barrage",
            b'{"text":"hi"}',
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(st_ct, 415)

        # Strict JSON int validation on like ID (reject bool, float, overflow)
        for bad_id in (True, False, 1.5, "1", 0, -5, (1 << 63) + 1):
            st_b, _, _ = self._req("POST", "/api/v1/barrage/like", {"id": bad_id})
            self.assertEqual(st_b, 400, f"Expected 400 for id={bad_id!r}")

        # Valid post stores hashed IP only
        st_p, _, res_p = self._req(
            "POST",
            "/api/v1/barrage",
            {"nick": "多军先锋", "side": "LONG", "coin": "BTC", "text": "正常发言检验"},
            headers={"X-Real-Client-IP": "203.0.113.10", "Origin": "https://danclub.duckdns.org"},
        )
        self.assertEqual(st_p, 201)
        self.assertNotIn("ip_hash", res_p["item"])
        conn = sqlite3.connect(str(self.db_path))
        ip_hashes = [r[0] for r in conn.execute("SELECT ip_hash FROM barrage_messages").fetchall()]
        conn.close()
        self.assertTrue(ip_hashes and all("203.0.113.10" not in h for h in ip_hashes))

    def test_rate_limits_spoof_resistance_and_bounded_admission(self) -> None:
        base_ts = 1_800_000_000
        st1, _ = self.store.post_message(
            client_ip="198.51.100.1", nick="A", side="LONG", coin="BTC", text="第一条冲锋", now_ts=base_ts
        )
        self.assertEqual(st1, 201)
        st2, r2 = self.store.post_message(
            client_ip="198.51.100.1", nick="A", side="LONG", coin="BTC", text="第二条太快", now_ts=base_ts + 2
        )
        self.assertEqual(st2, 429)
        self.assertIn("retry_after", r2)

        # Spoofing X-Forwarded-For does NOT bypass cooldown
        st_h1, _, _ = self._req(
            "POST",
            "/api/v1/barrage",
            {"nick": "Bot", "side": "SHORT", "coin": "BTC", "text": "测试伪造头一"},
            headers={"X-Forwarded-For": "1.1.1.1"},
        )
        self.assertEqual(st_h1, 201)
        st_h2, _, _ = self._req(
            "POST",
            "/api/v1/barrage",
            {"nick": "Bot", "side": "SHORT", "coin": "BTC", "text": "测试伪造头二"},
            headers={"X-Forwarded-For": "2.2.2.2"},
        )
        self.assertEqual(st_h2, 429)

        # Oversized payload rejected with 413, bad JSON with 400
        st_big, _, _ = self._req("POST", "/api/v1/barrage", b"{" + b"a" * 5000 + b"}")
        self.assertEqual(st_big, 413)
        st_bad, _, _ = self._req("POST", "/api/v1/barrage", b"{not-json")
        self.assertEqual(st_bad, 400)

        # Pre-thread bounded admission returns 503 immediately when saturated
        self.assertTrue(self.server._conn_gate._value == bs.MAX_CONCURRENT_REQUESTS)
        sat_srv = bs.create_server("127.0.0.1", 0, self.store, max_concurrent=1)
        sat_port = sat_srv.server_address[1]
        sat_t = threading.Thread(target=sat_srv.serve_forever, daemon=True)
        sat_t.start()
        try:
            # Exhaust the sole concurrency permit to verify immediate 503 rejection
            self.assertTrue(sat_srv._conn_gate.acquire(blocking=False))
            conn = http.client.HTTPConnection("127.0.0.1", sat_port, timeout=3)
            conn.request("GET", "/api/v1/barrage/health")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            self.assertEqual(resp.status, 503)
            sat_srv._conn_gate.release()
            # Permit recovered cleanly -> normal 200 resumes
            conn2 = http.client.HTTPConnection("127.0.0.1", sat_port, timeout=3)
            conn2.request("GET", "/api/v1/barrage/health")
            resp2 = conn2.getresponse()
            resp2.read()
            conn2.close()
            self.assertEqual(resp2.status, 200)
        finally:
            sat_srv.shutdown()
            sat_srv.server_close()
            sat_t.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
