"""Exercise the REAL `MikroTik` class (librouteros, binary API protocol) against a tiny in-process
fake RouterOS API server. It validates wire-level usage (queries, add/set/remove, custom commands,
traps, login) without hardware. It does NOT replace testing against the actual hEX router."""
import socket
import threading

from django.test import SimpleTestCase
from librouteros.protocol import decode_length, determine_length, encode_sentence

from network.mikrotik import MANAGED, MikroTik, RouterError


class FakeRouterOS:
    def __init__(self, user="djangoapi", password="secret"):
        self.user, self.password = user, password
        self.seq = 0
        self.lock = threading.Lock()
        self.tables = {
            "/ip/hotspot/user/profile": [{".id": "*P0", "name": "default", "rate-limit": ""}],
            "/ip/hotspot/user": [
                {".id": "*U0", "name": "wizard", "password": "x", "profile": "default", "comment": "", "disabled": "false",
                 "bytes-in": "0", "bytes-out": "0", "limit-bytes-total": "0"},
                {".id": "*U1", "name": "test", "password": "test", "profile": "default", "comment": "", "disabled": "false",
                 "bytes-in": "0", "bytes-out": "0", "limit-bytes-total": "0"},
            ],
            "/ip/hotspot/active": [],
            "/ip/hotspot/cookie": [],
            "/ip/hotspot/host": [{".id": "*H1", "address": "192.168.88.77", "mac-address": "aa:bb:cc:00:00:77"}],
            "/ip/hotspot": [{".id": "*S1", "name": "hotspot1", "interface": "bridge", "profile": "hsprof1", "disabled": "false"}],
        }
        self.log = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        self.running = True
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.srv.close()
        except OSError:
            pass

    # ---- wire -------------------------------------------------------------------------
    @staticmethod
    def _recv(conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    def _read_sentence(self, conn):
        words = []
        while True:
            first = self._recv(conn, 1)
            extra = determine_length(first)
            length = decode_length(first + (self._recv(conn, extra) if extra else b""))
            if length == 0:
                return words
            words.append(self._recv(conn, length).decode("ascii"))

    @staticmethod
    def _send(conn, *words):
        conn.sendall(encode_sentence(*words, encoding="ascii"))

    def _serve(self):
        while self.running:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            while True:
                sentence = self._read_sentence(conn)
                cmd, attrs, queries = sentence[0], {}, []
                for w in sentence[1:]:
                    if w.startswith("="):
                        _, k, v = w.split("=", 2)
                        attrs[k] = v
                    elif w.startswith("?"):
                        queries.append(w)
                self.log.append((cmd, dict(attrs), list(queries)))
                with self.lock:
                    self.sentence_words = sentence
                    self._dispatch(conn, cmd, attrs, queries)
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()

    # ---- behaviour --------------------------------------------------------------------
    def _trap(self, conn, msg):
        self._send(conn, "!trap", f"=message={msg}")
        self._send(conn, "!done")

    @staticmethod
    def _norm(v):
        return {"yes": "true", "no": "false"}.get(v, v)

    def _dispatch(self, conn, cmd, attrs, queries):
        if cmd == "/login":
            if attrs.get("name") == self.user and attrs.get("password") == self.password:
                return self._send(conn, "!done")
            return self._trap(conn, "invalid user name or password (6)")
        path, _, verb = cmd.rpartition("/")
        table = self.tables.get(path)
        if cmd == "/system/resource/print":
            self._send(conn, "!re", "=cpu-load=7", "=uptime=1d2h3m4s", "=version=7.20.8 (stable)", "=board-name=E50UG",
                       "=free-memory=1000", "=total-memory=2000")
            return self._send(conn, "!done")
        if cmd == "/interface/monitor-traffic":
            self._send(conn, "!re", f"=name={attrs.get('interface')}", "=rx-bits-per-second=4000000", "=tx-bits-per-second=500000")
            return self._send(conn, "!done")
        if path == "/ip/hotspot/user" and verb == "reset-counters":
            for row in table:
                if row[".id"] == attrs[".id"]:
                    row["bytes-in"] = row["bytes-out"] = "0"
            return self._send(conn, "!done")
        if path == "/ip/hotspot/active" and verb == "login":
            user = next((u for u in self.tables["/ip/hotspot/user"] if u["name"] == attrs.get("user")), None)
            if user is None or user["password"] != attrs.get("password"):
                return self._trap(conn, "invalid username or password")
            if user["disabled"] == "true":
                return self._trap(conn, "user is disabled")
            self.seq += 1
            self.tables["/ip/hotspot/active"].append(
                {".id": f"*A{self.seq}", "user": user["name"], "address": attrs["ip"], "mac-address": attrs["mac-address"],
                 "uptime": "1m30s", "bytes-in": "100", "bytes-out": "900", "login-by": "http-pap"})
            self.tables["/ip/hotspot/cookie"].append({".id": f"*C{self.seq}", "user": user["name"], "mac-address": attrs["mac-address"]})
            return self._send(conn, "!done")
        if table is None:
            return self._trap(conn, f"no such command prefix {cmd}")
        if verb == "print":
            rows = table
            for q in queries:
                if q.startswith("?="):
                    _, k, v = q.split("=", 2)
                    rows = [r for r in rows if str(r.get(k)) == v]
            if path == "/ip/hotspot/user/profile" and any("comment" in w for w in self.sentence_words):
                return self._trap(conn, "unknown parameter comment")
            for r in rows:
                self._send(conn, "!re", *[f"={k}={v}" for k, v in r.items()])
            return self._send(conn, "!done")
        if path == "/ip/hotspot/user/profile" and verb in ("add", "set") and "comment" in attrs:
            return self._trap(conn, "unknown parameter comment")  # real RouterOS: profiles have no comment
        if verb == "add":
            self.seq += 1
            row = {".id": f"*N{self.seq}", **{k: self._norm(v) for k, v in attrs.items()}}
            row.setdefault("bytes-in", "0")
            row.setdefault("bytes-out", "0")
            table.append(row)
            return self._send(conn, "!done", f"=ret={row['.id']}")
        if verb == "set":
            for r in table:
                if r[".id"] == attrs[".id"]:
                    r.update({k: self._norm(v) for k, v in attrs.items() if k != ".id"})
                    return self._send(conn, "!done")
            return self._trap(conn, "no such item")
        if verb == "remove":
            ids = attrs[".id"].split(",")
            before = len(table)
            table[:] = [r for r in table if r[".id"] not in ids]
            if len(table) == before:
                return self._trap(conn, "no such item")
            return self._send(conn, "!done")
        return self._trap(conn, f"unknown verb {verb}")

    def get_user(self, name):
        return next((u for u in self.tables["/ip/hotspot/user"] if u["name"] == name), None)


class RealRouterProtocolTests(SimpleTestCase):
    def setUp(self):
        self.fake = FakeRouterOS()
        self.addCleanup(self.fake.stop)
        self.router = MikroTik(host="127.0.0.1", port=self.fake.port, user="djangoapi", password="secret", timeout=2, server="hotspot1")

    def open(self):
        return self.router.connect()

    def test_connect_ok_and_reports_status(self):
        with self.router as r:
            res = r.resources()
            self.assertEqual((res["cpu_load"], res["version"], res["board"]), (7, "7.20.8 (stable)", "E50UG"))
            self.assertEqual(res["uptime"], "1d2h3m4s")
            hs = r.server_status()
            self.assertTrue(hs["found"] and not hs["disabled"])
            self.assertEqual(hs["interface"], "bridge")
            self.assertEqual(r.traffic(), {"rx_bps": 4_000_000, "tx_bps": 500_000})

    def test_wrong_password_is_a_friendly_error(self):
        bad = MikroTik(host="127.0.0.1", port=self.fake.port, user="djangoapi", password="WRONG", timeout=2)
        with self.assertRaises(RouterError) as cm:
            bad.connect()
        self.assertIn("rejected the API username or password", str(cm.exception))

    def test_unreachable_router_is_a_friendly_error(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listens here now
        with self.assertRaises(RouterError) as cm:
            MikroTik(host="127.0.0.1", port=port, user="u", password="p", timeout=1).connect()
        self.assertRegex(str(cm.exception), r"Cannot reach the router|did not answer in time")

    def test_ensure_profile_adds_then_updates(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.ensure_profile("cdk-standard", "3M/3M", session_timeout="1h")
        rows = [p for p in self.fake.tables["/ip/hotspot/user/profile"] if p["name"] == "cdk-standard"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["rate-limit"], rows[0]["shared-users"], rows[0].get("comment"), rows[0]["session-timeout"]),
                         ("3M/3M", "1", None, "1h"))

    def test_ensure_profile_refuses_unmanaged_profile(self):
        with self.router as r:
            with self.assertRaises(RouterError):
                r.ensure_profile("default", "1M/1M")

    def test_upsert_user_create_update_and_reset_counters(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("2026-001", "pw1", "cdk-standard", limit_bytes_total=5000)
            u = self.fake.get_user("2026-001")
            self.assertEqual((u["password"], u["profile"], u["comment"], u["limit-bytes-total"], u["disabled"]),
                             ("pw1", "cdk-standard", MANAGED, "5000", "false"))
            u["bytes-in"], u["bytes-out"] = "10", "20"
            r.upsert_user("2026-001", "pw2", "cdk-standard", limit_bytes_total=4000, reset_counters=True)
            u = self.fake.get_user("2026-001")
            self.assertEqual((u["password"], u["limit-bytes-total"], u["bytes-in"], u["bytes-out"]), ("pw2", "4000", "0", "0"))
            # keep_limit leaves the limit alone (used while a session is live)
            r.upsert_user("2026-001", "pw2", "cdk-standard", limit_bytes_total=1, keep_limit=True)
            self.assertEqual(self.fake.get_user("2026-001")["limit-bytes-total"], "4000")
        self.assertEqual(len([x for x in self.fake.tables["/ip/hotspot/user"] if x["name"] == "2026-001"]), 1)

    def test_unlimited_quota_sends_zero(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("u1", "p", "cdk-standard", limit_bytes_total=None)
        self.assertEqual(self.fake.get_user("u1")["limit-bytes-total"], "0")

    def test_guard_unmanaged_users_but_take_over_test(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            with self.assertRaises(RouterError):
                r.upsert_user("wizard", "p", "cdk-standard")
            with self.assertRaises(RouterError):
                r.disable_user("wizard")
            r.upsert_user("test", "newpw", "cdk-standard", limit_bytes_total=100)
        t = self.fake.get_user("test")
        self.assertEqual((t["password"], t["comment"]), ("newpw", MANAGED))
        self.assertEqual(self.fake.get_user("wizard")["password"], "x")  # untouched

    def test_disable_user(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("u1", "p", "cdk-standard")
            self.assertTrue(r.disable_user("u1"))
            self.assertFalse(r.disable_user("nobody"))
        self.assertEqual(self.fake.get_user("u1")["disabled"], "true")

    def test_login_device_active_sessions_and_kick(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("u1", "pw", "cdk-standard")
            r.login_device("u1", "pw", "aa:bb:cc:dd:ee:01", "192.168.88.50")
            actives = r.active_sessions()
            self.assertEqual(len(actives), 1)
            a = actives[0]
            self.assertEqual((a["user"], a["address"], a["mac"], a["uptime_seconds"], a["bytes_in"], a["bytes_out"]),
                             ("u1", "192.168.88.50", "AA:BB:CC:DD:EE:01", 90, 100, 900))
            r.kick(a["id"], user="u1")
            self.assertEqual(r.active_sessions(), [])
        self.assertEqual(self.fake.tables["/ip/hotspot/cookie"], [])  # MAC cookie removed so it cannot auto re-login
        cmd, attrs, _ = next(x for x in self.fake.log if x[0] == "/ip/hotspot/active/login")
        self.assertEqual(attrs, {"user": "u1", "password": "pw", "mac-address": "aa:bb:cc:dd:ee:01", "ip": "192.168.88.50"})

    def test_login_failure_is_friendly(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("u1", "pw", "cdk-standard", disabled=True)
            with self.assertRaises(RouterError) as cm:
                r.login_device("u1", "pw", "aa:bb:cc:dd:ee:01", "192.168.88.50")
            self.assertIn("Could not log the device in", str(cm.exception))

    def test_kick_user_and_counters_and_lookup(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("u1", "pw", "cdk-standard", limit_bytes_total=7777)
            r.login_device("u1", "pw", "aa:bb:cc:dd:ee:01", "192.168.88.50")
            self.assertEqual(r.kick_user("u1"), 1)
            self.assertEqual(r.kick_user("u1"), 0)  # nothing left, still safe
            self.assertEqual(r.user_counters("u1"), {"bytes_in": 0, "bytes_out": 0, "limit": 7777})
            self.assertIsNone(r.user_counters("ghost"))
            self.assertEqual(r.host_lookup("192.168.88.77"), "AA:BB:CC:00:00:77")
            self.assertEqual(r.host_lookup("1.2.3.4"), "")

    def test_list_managed_users_excludes_foreign_ones(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("u1", "pw", "cdk-standard")
            names = {u["name"] for u in r.list_managed_users()}
        self.assertEqual(names, {"u1", "test"})  # 'wizard' is not ours; 'test' is taken over by design

    def test_lookup_uses_a_server_side_query(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
        queries = [q for cmd, _, q in self.fake.log if cmd == "/ip/hotspot/user/profile/print" and q]
        self.assertTrue(any("?=name=cdk-standard" in q for q in queries))

    def test_remove_user(self):
        with self.router as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("u1", "pw", "cdk-standard")
            self.assertTrue(r.remove_user("u1"))
        self.assertIsNone(self.fake.get_user("u1"))

    def test_connection_lost_mid_call_is_a_router_error(self):
        r = self.router.connect()
        self.fake.stop()
        r.api.close()  # simulate dropped link
        with self.assertRaises(RouterError):
            r.resources()
