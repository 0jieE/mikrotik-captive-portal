"""All RouterOS API access lives here.

Two interchangeable back-ends share the same interface:

* ``MikroTik``      - real router through ``librouteros`` (plain API, port 8728)
* ``MockMikroTik``  - in-memory / JSON-file fake used when ``MIKROTIK_MOCK=True`` (and in tests)

Use ``get_router()`` to obtain the right one and always call it as a context manager::

    with get_router() as router:
        router.active_sessions()

Rules: Django is the source of truth. Only router entries whose comment is ``cdk-managed``
(plus the wizard's ``test`` user, which Django takes over) are ever modified.
"""
from __future__ import annotations

import json
import random
import re
import socket
import threading
import time
from pathlib import Path

from django.conf import settings

MANAGED = "cdk-managed"
TAKEOVER_USERS = {"test"}


class RouterError(Exception):
    """Friendly, user-presentable router failure."""


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
_UPTIME_RE = re.compile(r"(\d+)([wdhms])")
_UNIT = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_uptime(value) -> int:
    """'1w2d3h4m5s' -> seconds."""
    if isinstance(value, (int, float)):
        return int(value)
    if not value:
        return 0
    return sum(int(n) * _UNIT[u] for n, u in _UPTIME_RE.findall(str(value)))


def format_uptime(seconds: int) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    out = (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"
    return out


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


PROFILE_PREFIX = "cdk-"


def _check_managed(existing: dict | None, name: str, kind: str):
    """Refuse to touch router entries Django does not own.

    Hotspot users are ours when their comment is ``cdk-managed`` (plus the wizard's ``test`` user).
    Hotspot user *profiles* have no comment field in RouterOS, so ours are recognised by the ``cdk-`` name prefix.
    """
    if existing is None:
        return
    if kind == "profile":
        if name.startswith(PROFILE_PREFIX):
            return
    else:
        if existing.get("comment") == MANAGED:
            return
        if kind == "user" and name in TAKEOVER_USERS:
            return
    raise RouterError(f"Refusing to modify {kind} '{name}' on the router: it is not managed by this portal.")


# --------------------------------------------------------------------------------------
# real router
# --------------------------------------------------------------------------------------
class MikroTik:
    is_mock = False

    def __init__(self, host=None, port=None, user=None, password=None, timeout=None, server=None):
        self.host = host or settings.MIKROTIK_HOST
        self.port = port or settings.MIKROTIK_PORT
        self.user = user or settings.MIKROTIK_USER
        self.password = password if password is not None else settings.MIKROTIK_PASSWORD
        self.timeout = timeout or settings.MIKROTIK_TIMEOUT
        self.server = server or settings.MIKROTIK_HOTSPOT_SERVER
        self.api = None

    # connection ---------------------------------------------------------------------
    def connect(self):
        import librouteros
        from librouteros.exceptions import LibRouterosError

        try:
            self.api = librouteros.connect(
                host=self.host,
                username=self.user,
                password=self.password,
                port=self.port,
                timeout=self.timeout,
            )
        except (socket.timeout, TimeoutError):
            raise RouterError(
                f"The router at {self.host}:{self.port} did not answer in time. "
                "Check the network cable/IP and that the API service is enabled."
            ) from None
        except (ConnectionRefusedError, OSError) as e:
            raise RouterError(
                f"Cannot reach the router at {self.host}:{self.port} ({e.__class__.__name__}). "
                "Check the network cable/IP and that the API service is enabled."
            ) from None
        except LibRouterosError as e:
            msg = str(e).lower()
            if "invalid user" in msg or "cannot log in" in msg or "password" in msg:
                raise RouterError("The router rejected the API username or password (check MIKROTIK_USER / MIKROTIK_PASSWORD in .env).") from None
            raise RouterError(f"Router login failed: {e}") from None
        return self

    def close(self):
        if self.api is not None:
            try:
                self.api.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
            self.api = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # low level ----------------------------------------------------------------------
    def _run(self, fn):
        from librouteros.exceptions import LibRouterosError

        if self.api is None:
            raise RouterError("Not connected to the router.")
        try:
            return fn(self.api)
        except (socket.timeout, TimeoutError):
            raise RouterError("The router stopped responding (timeout).") from None
        except (ConnectionError, OSError):
            raise RouterError("The connection to the router was lost.") from None
        except LibRouterosError as e:
            raise RouterError(f"Router rejected the command: {e}") from None

    @staticmethod
    def _find(api, path: tuple[str, ...], **where) -> dict | None:
        from librouteros.query import Key

        # profiles have no 'comment' property on RouterOS, so only ask for it elsewhere
        keys = [Key(".id"), *[Key(k) for k in where]]
        if path[-1] != "profile":
            keys.append(Key("comment"))
        query = (
            api.path(*path)
            .select(*keys)
            .where(*[Key(k) == v for k, v in where.items()], *([["?#&"]] * (len(where) - 1)))
        )
        for item in query:
            return item
        return None

    # profiles / users ---------------------------------------------------------------
    def ensure_profile(self, name, rate_limit, session_timeout="", shared_users=1):
        def op(api):
            path = api.path("ip", "hotspot", "user", "profile")
            existing = self._find(api, ("ip", "hotspot", "user", "profile"), name=name)
            _check_managed(existing, name, "profile")
            fields = {"rate-limit": rate_limit, "shared-users": shared_users}
            fields["session-timeout"] = session_timeout or "0s"
            if existing:
                path.update(**{".id": existing[".id"]}, **fields)
            else:
                path.add(name=name, **fields)
            return True

        return self._run(op)

    def upsert_user(self, name, password, profile, limit_bytes_total=None, disabled=False, reset_counters=False, keep_limit=False):
        def op(api):
            path = api.path("ip", "hotspot", "user")
            existing = self._find(api, ("ip", "hotspot", "user"), name=name)
            _check_managed(existing, name, "user")
            fields = {
                "password": password,
                "profile": profile,
                "limit-bytes-total": int(limit_bytes_total or 0),
                "comment": MANAGED,
                "disabled": bool(disabled),
            }
            if existing:
                if keep_limit:
                    fields.pop("limit-bytes-total")
                path.update(**{".id": existing[".id"]}, **fields)
                uid = existing[".id"]
            else:
                uid = path.add(name=name, **fields)
            if reset_counters:
                tuple(path("reset-counters", **{".id": uid}))
            return True

        return self._run(op)

    def disable_user(self, name) -> bool:
        def op(api):
            existing = self._find(api, ("ip", "hotspot", "user"), name=name)
            if existing is None:
                return False
            _check_managed(existing, name, "user")
            api.path("ip", "hotspot", "user").update(**{".id": existing[".id"], "disabled": True})
            return True

        return self._run(op)

    def remove_user(self, name) -> bool:
        def op(api):
            existing = self._find(api, ("ip", "hotspot", "user"), name=name)
            if existing is None:
                return False
            _check_managed(existing, name, "user")
            api.path("ip", "hotspot", "user").remove(existing[".id"])
            return True

        return self._run(op)

    def list_managed_users(self) -> list[dict]:
        def op(api):
            users = []
            for u in api.path("ip", "hotspot", "user"):
                if u.get("comment") == MANAGED or u.get("name") in TAKEOVER_USERS:
                    users.append({"name": u.get("name"), "disabled": bool(u.get("disabled")), "id": u.get(".id")})
            return users

        return self._run(op)

    # hotspot sessions ---------------------------------------------------------------
    def login_device(self, user, password, mac, ip):
        def op(api):
            tuple(api.path("ip", "hotspot", "active")("login", **{"user": user, "password": password, "mac-address": mac, "ip": ip}))
            return True

        try:
            return self._run(op)
        except RouterError as e:
            raise RouterError(f"Could not log the device in on the hotspot: {e}") from None

    def active_sessions(self) -> list[dict]:
        def op(api):
            rows = []
            for a in api.path("ip", "hotspot", "active"):
                rows.append(
                    {
                        "id": a.get(".id", ""),
                        "user": a.get("user", ""),
                        "address": a.get("address", ""),
                        "mac": (a.get("mac-address") or "").upper(),
                        "uptime_seconds": parse_uptime(a.get("uptime")),
                        "bytes_in": _int(a.get("bytes-in")),
                        "bytes_out": _int(a.get("bytes-out")),
                        "login_by": a.get("login-by", ""),
                    }
                )
            return rows

        return self._run(op)

    def kick(self, active_id, user=None):
        """Remove an active hotspot entry (and the user's MAC cookies so it can't silently re-login)."""

        def op(api):
            try:
                api.path("ip", "hotspot", "active").remove(active_id)
            except Exception:  # entry may already be gone  # noqa: BLE001
                pass
            if user:
                cookies = api.path("ip", "hotspot", "cookie")
                for c in list(cookies):
                    if c.get("user") == user:
                        cookies.remove(c[".id"])
            return True

        return self._run(op)

    def kick_user(self, username) -> int:
        n = 0
        for a in self.active_sessions():
            if a["user"] == username:
                self.kick(a["id"], user=username)
                n += 1
        if n == 0:
            # still clear cookies
            self._run(lambda api: [api.path("ip", "hotspot", "cookie").remove(c[".id"]) for c in list(api.path("ip", "hotspot", "cookie")) if c.get("user") == username])
        return n

    def user_counters(self, name) -> dict | None:
        """Hotspot user's accumulated counters (reset at every portal login, so = last session)."""

        def op(api):
            from librouteros.query import Key

            q = (
                api.path("ip", "hotspot", "user")
                .select(Key("bytes-in"), Key("bytes-out"), Key("limit-bytes-total"))
                .where(Key("name") == name)
            )
            for u in q:
                return {
                    "bytes_in": _int(u.get("bytes-in")),
                    "bytes_out": _int(u.get("bytes-out")),
                    "limit": _int(u.get("limit-bytes-total")),
                }
            return None

        return self._run(op)

    def host_lookup(self, ip) -> str:
        def op(api):
            for h in api.path("ip", "hotspot", "host"):
                if h.get("address") == ip:
                    return (h.get("mac-address") or "").upper()
            return ""

        return self._run(op)

    # system -------------------------------------------------------------------------
    def resources(self) -> dict:
        def op(api):
            r = next(iter(api.path("system", "resource")), {})
            return {
                "cpu_load": _int(r.get("cpu-load")),
                "uptime": r.get("uptime", ""),
                "version": r.get("version", ""),
                "board": r.get("board-name", ""),
                "free_memory": _int(r.get("free-memory")),
                "total_memory": _int(r.get("total-memory")),
            }

        return self._run(op)

    def server_status(self) -> dict:
        def op(api):
            for s in api.path("ip", "hotspot"):
                if s.get("name") == self.server:
                    return {
                        "name": s.get("name"),
                        "interface": s.get("interface", ""),
                        "profile": s.get("profile", ""),
                        "disabled": bool(s.get("disabled")),
                        "found": True,
                    }
            return {"name": self.server, "found": False, "disabled": True, "interface": "", "profile": ""}

        return self._run(op)

    def fasttrack_active(self) -> bool:
        """True when an enabled FastTrack rule exists. FastTrack bypasses queues, so speed limits would NOT apply."""

        def op(api):
            from librouteros.query import Key

            q = (
                api.path("ip", "firewall", "filter")
                .select(Key("action"), Key("disabled"))
                .where(Key("action") == "fasttrack-connection")
            )
            return any(not row.get("disabled") for row in q)

        return self._run(op)

    def traffic(self) -> dict:
        """Current WAN throughput in bits per second (rx = download into the LAN)."""

        def op(api):
            rows = list(api.path("interface")("monitor-traffic", interface=settings.MIKROTIK_WAN_INTERFACE, once=True))
            r = rows[0] if rows else {}
            return {"rx_bps": _int(r.get("rx-bits-per-second")), "tx_bps": _int(r.get("tx-bits-per-second"))}

        try:
            return self._run(op)
        except RouterError:
            return {"rx_bps": None, "tx_bps": None}


# --------------------------------------------------------------------------------------
# mock router
# --------------------------------------------------------------------------------------
class MockMikroTik:
    """Fake router. State persists in a JSON file when settings.MIKROTIK_MOCK_STATE_FILE is set
    (so runserver and the sync_sessions command see the same router), otherwise in process memory."""

    is_mock = True
    _memory_state: dict | None = None
    _lock = threading.RLock()

    def __init__(self, **_):
        self.server = settings.MIKROTIK_HOTSPOT_SERVER
        self.state: dict = {}
        self.online = True

    # state --------------------------------------------------------------------------
    @classmethod
    def _state_file(cls):
        f = getattr(settings, "MIKROTIK_MOCK_STATE_FILE", None)
        return Path(f) if f else None

    @classmethod
    def reset(cls):
        cls._memory_state = None
        f = cls._state_file()
        if f and f.exists():
            f.unlink()

    @staticmethod
    def _fresh():
        return {"started": time.time(), "seq": 0, "users": {}, "profiles": {}, "active": [], "cookies": [], "offline": False}

    def _load(self):
        f = self._state_file()
        if f:
            try:
                self.state = json.loads(f.read_text())
                return
            except (OSError, ValueError):
                self.state = self._fresh()
                return
        if MockMikroTik._memory_state is None:
            MockMikroTik._memory_state = self._fresh()
        self.state = MockMikroTik._memory_state

    def _save(self):
        f = self._state_file()
        if f:
            f.write_text(json.dumps(self.state))
        else:
            MockMikroTik._memory_state = self.state

    def _next_id(self):
        self.state["seq"] += 1
        return f"*{self.state['seq']:X}"

    # connection ---------------------------------------------------------------------
    def connect(self):
        with self._lock:
            self._load()
            if self.state.get("offline"):
                raise RouterError("Cannot reach the router at 192.168.88.1:8728 (mock router set offline).")
        return self

    def close(self):
        pass

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # test / demo helpers ------------------------------------------------------------
    def set_offline(self, offline=True):
        with self._lock:
            self._load()
            self.state["offline"] = offline
            self._save()

    def add_traffic(self, user, bytes_in=0, bytes_out=0):
        """Simulate a client transferring data; mimics the router enforcing limit-bytes-total."""
        with self._lock:
            self._load()
            u = self.state["users"].get(user)
            for a in list(self.state["active"]):
                if a["user"] == user:
                    a["bytes_in"] += bytes_in
                    a["bytes_out"] += bytes_out
            if u:
                u["bytes_in"] += bytes_in
                u["bytes_out"] += bytes_out
                limit = u.get("limit_bytes_total", 0)
                if limit and u["bytes_in"] + u["bytes_out"] >= limit:
                    self.state["active"] = [a for a in self.state["active"] if a["user"] != user]
            self._save()

    def vanish(self, user):
        """Make a session disappear from the router without Django asking (idle timeout, etc.)."""
        with self._lock:
            self._load()
            self.state["active"] = [a for a in self.state["active"] if a["user"] != user]
            self._save()

    def inject_active(self, user, mac, ip):
        """Create an active entry Django did not create (e.g. a MAC-cookie auto login)."""
        with self._lock:
            self._load()
            self.state["active"].append(
                {"id": self._next_id(), "user": user, "address": ip, "mac": mac.upper(), "started": time.time(), "bytes_in": 0, "bytes_out": 0, "login_by": "mac-cookie"}
            )
            self._save()

    # interface ----------------------------------------------------------------------
    def ensure_profile(self, name, rate_limit, session_timeout="", shared_users=1):
        with self._lock:
            self._load()
            _check_managed(self.state["profiles"].get(name), name, "profile")
            self.state["profiles"][name] = {
                "name": name,
                "rate-limit": rate_limit,
                "session-timeout": session_timeout,
                "shared-users": shared_users,
            }
            self._save()
        return True

    def upsert_user(self, name, password, profile, limit_bytes_total=None, disabled=False, reset_counters=False, keep_limit=False):
        with self._lock:
            self._load()
            existing = self.state["users"].get(name)
            _check_managed(existing, name, "user")
            if profile not in self.state["profiles"]:
                raise RouterError(f"Router rejected the command: no such profile '{profile}'")
            u = existing or {"name": name, "bytes_in": 0, "bytes_out": 0}
            u.update(password=password, profile=profile, disabled=bool(disabled), comment=MANAGED)
            if not (keep_limit and existing):
                u["limit_bytes_total"] = int(limit_bytes_total or 0)
            if reset_counters:
                u["bytes_in"] = u["bytes_out"] = 0
            self.state["users"][name] = u
            self._save()
        return True

    def disable_user(self, name) -> bool:
        with self._lock:
            self._load()
            u = self.state["users"].get(name)
            if u is None:
                return False
            _check_managed(u, name, "user")
            u["disabled"] = True
            self._save()
        return True

    def remove_user(self, name) -> bool:
        with self._lock:
            self._load()
            u = self.state["users"].get(name)
            if u is None:
                return False
            _check_managed(u, name, "user")
            del self.state["users"][name]
            self._save()
        return True

    def list_managed_users(self) -> list[dict]:
        with self._lock:
            self._load()
            return [{"name": n, "disabled": u["disabled"], "id": n} for n, u in self.state["users"].items() if u.get("comment") == MANAGED]

    def login_device(self, user, password, mac, ip):
        with self._lock:
            self._load()
            u = self.state["users"].get(user)
            if u is None or u["password"] != password:
                raise RouterError("Could not log the device in on the hotspot: invalid username or password")
            if u["disabled"]:
                raise RouterError("Could not log the device in on the hotspot: user is disabled")
            limit = u.get("limit_bytes_total", 0)
            if limit and u["bytes_in"] + u["bytes_out"] >= limit:
                raise RouterError("Could not log the device in on the hotspot: data limit reached")
            if any(a["user"] == user for a in self.state["active"]):
                raise RouterError("Could not log the device in on the hotspot: shared users limit reached")
            self.state["active"].append(
                {"id": self._next_id(), "user": user, "address": ip, "mac": mac.upper(), "started": time.time(), "bytes_in": 0, "bytes_out": 0, "login_by": "http-pap"}
            )
            self.state["cookies"].append({"user": user, "mac": mac.upper()})
            self._save()
        return True

    def active_sessions(self) -> list[dict]:
        with self._lock:
            self._load()
            now = time.time()
            return [
                {
                    "id": a["id"],
                    "user": a["user"],
                    "address": a["address"],
                    "mac": a["mac"],
                    "uptime_seconds": int(now - a["started"]),
                    "bytes_in": a["bytes_in"],
                    "bytes_out": a["bytes_out"],
                    "login_by": a.get("login_by", ""),
                }
                for a in self.state["active"]
            ]

    def kick(self, active_id, user=None):
        with self._lock:
            self._load()
            self.state["active"] = [a for a in self.state["active"] if a["id"] != active_id]
            if user:
                self.state["cookies"] = [c for c in self.state["cookies"] if c["user"] != user]
            self._save()
        return True

    def kick_user(self, username) -> int:
        with self._lock:
            self._load()
            n = len([a for a in self.state["active"] if a["user"] == username])
            self.state["active"] = [a for a in self.state["active"] if a["user"] != username]
            self.state["cookies"] = [c for c in self.state["cookies"] if c["user"] != username]
            self._save()
        return n

    def user_counters(self, name) -> dict | None:
        with self._lock:
            self._load()
            u = self.state["users"].get(name)
            if u is None:
                return None
            return {"bytes_in": u["bytes_in"], "bytes_out": u["bytes_out"], "limit": u.get("limit_bytes_total", 0)}

    def host_lookup(self, ip) -> str:
        return ""

    def resources(self) -> dict:
        with self._lock:
            self._load()
            up = int(time.time() - self.state["started"])
            n = len(self.state["active"])
        return {
            "cpu_load": min(3 + n * 2 + random.randint(0, 4), 100),
            "uptime": format_uptime(up),
            "version": "7.20.8 (mock)",
            "board": "hEX refresh E50UG (mock)",
            "free_memory": 410 * 1024 * 1024,
            "total_memory": 512 * 1024 * 1024,
        }

    def server_status(self) -> dict:
        return {"name": self.server, "interface": "bridge", "profile": "hsprof1", "disabled": False, "found": True}

    def fasttrack_active(self) -> bool:
        with self._lock:
            self._load()
            return bool(self.state.get("fasttrack", False))

    def set_fasttrack(self, on=True):
        with self._lock:
            self._load()
            self.state["fasttrack"] = on
            self._save()

    def traffic(self) -> dict:
        with self._lock:
            self._load()
            n = len(self.state["active"])
        return {"rx_bps": n * random.randint(150_000, 900_000), "tx_bps": n * random.randint(30_000, 200_000)}


# --------------------------------------------------------------------------------------
def get_router():
    """Return an (unconnected) router object. Use as ``with get_router() as r:``."""
    if settings.MIKROTIK_MOCK:
        return MockMikroTik()
    return MikroTik()

