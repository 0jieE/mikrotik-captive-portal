"""Business rules that tie Django (source of truth) to the router (mirror)."""
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.audit import audit
from academics.models import BandwidthPolicy, Semester
from instructors.models import MB, SemesterAccount, Instructor

from .mikrotik import RouterError, get_router
from .models import Session

MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


# --------------------------------------------------------------------------------------
# access rules
# --------------------------------------------------------------------------------------
@dataclass
class AccessResult:
    allowed: bool
    code: str
    message: str
    account: SemesterAccount | None = None


MESSAGES = {
    "ok": "Access granted.",
    "inactive_user": "Your account is disabled. Please contact the ICT office.",
    "not_instructor": "This is not an instructor account.",
    "pending_approval": "Your registration is waiting for approval by the ICT office. You can connect as soon as an administrator approves it.",
    "rejected": "Your registration was not approved. Please contact the ICT office.",
    "no_semester": "There is no active semester right now, so internet access is closed.",
    "no_account": "You have no internet account for the current semester. Please contact the ICT office.",
    "account_disabled": "Your internet account is disabled. Please contact the ICT office.",
    "quota": "Your data quota for this semester is used up.",
}


def _deny(code, account=None):
    return AccessResult(False, code, MESSAGES[code], account)


def account_access(account: SemesterAccount) -> AccessResult:
    """Access rules for one SemesterAccount:
    user active + registration approved + semester active + account active + quota not exceeded."""
    user = account.instructor.user
    if not user.is_active:
        return _deny("inactive_user", account)
    if account.instructor.approval_status == Instructor.PENDING:
        return _deny("pending_approval", account)
    if account.instructor.approval_status == Instructor.REJECTED:
        return _deny("rejected", account)
    if not account.semester.is_active:
        return _deny("no_semester", account)
    if not account.is_active:
        return _deny("account_disabled", account)
    if account.quota_exceeded:
        return _deny("quota", account)
    return AccessResult(True, "ok", MESSAGES["ok"], account)


def evaluate_access(user) -> AccessResult:
    if not user.is_active:
        return _deny("inactive_user")
    instructor = getattr(user, "instructor", None)
    if instructor is None or not user.is_instructor:
        return _deny("not_instructor")
    if instructor.approval_status == Instructor.PENDING:
        return _deny("pending_approval")
    if instructor.approval_status == Instructor.REJECTED:
        return _deny("rejected")
    semester = Semester.get_active()
    if semester is None:
        return _deny("no_semester")
    account = (
        SemesterAccount.objects.filter(instructor=instructor, semester=semester)
        .select_related("policy", "semester", "instructor__user")
        .first()
    )
    if account is None:
        return _deny("no_account")
    return account_access(account)


# --------------------------------------------------------------------------------------
# router helpers
# --------------------------------------------------------------------------------------
@contextmanager
def use_router(router=None):
    if router is not None:
        yield router
    else:
        with get_router() as r:
            yield r


def try_router(fn):
    """Run fn(router) and return an error string (or None). Never raises RouterError."""
    try:
        with get_router() as r:
            fn(r)
        return None
    except RouterError as e:
        return str(e)


def sync_policy(policy: BandwidthPolicy, router=None) -> tuple[bool, str]:
    try:
        with use_router(router) as r:
            r.ensure_profile(policy.mikrotik_profile_name, policy.rate_limit, policy.session_timeout)
        policy.sync_status, policy.sync_message = BandwidthPolicy.SYNC_OK, ""
        ok = True
    except RouterError as e:
        policy.sync_status, policy.sync_message = BandwidthPolicy.SYNC_FAILED, str(e)[:255]
        ok = False
    policy.last_synced_at = timezone.now()
    policy.save(update_fields=["sync_status", "sync_message", "last_synced_at"])
    return ok, policy.sync_message


def push_account(router, account: SemesterAccount, active_users: set[str] | None = None, allowed=None):
    """Mirror one account to the router: enabled user when access is allowed, disabled otherwise."""
    username = account.instructor.user.username
    result = account_access(account) if allowed is None else allowed
    if not result.allowed:
        router.disable_user(username)
        return result
    if active_users is None:
        active_users = {a["user"] for a in router.active_sessions()}
    on_router = username in active_users
    router.ensure_profile(account.policy.mikrotik_profile_name, account.policy.rate_limit, account.policy.session_timeout)
    router.upsert_user(
        username,
        account.hotspot_password,
        account.policy.mikrotik_profile_name,
        limit_bytes_total=account.remaining_bytes,
        disabled=False,
        reset_counters=not on_router,
        keep_limit=on_router,
    )
    return result


# --------------------------------------------------------------------------------------
# usage accounting / sessions
# --------------------------------------------------------------------------------------
def apply_usage(session: Session, bytes_in: int, bytes_out: int) -> int:
    """Update a session's byte counters and add the delta to the semester account. Returns delta."""
    new_total = bytes_in + bytes_out
    old_total = session.bytes_in + session.bytes_out
    delta = new_total - old_total if new_total >= old_total else new_total  # counter reset => count from 0
    session.bytes_in, session.bytes_out = bytes_in, bytes_out
    session.consumption = round(new_total / MB, 3)
    if delta > 0 and session.semester_account_id:
        SemesterAccount.objects.filter(pk=session.semester_account_id).update(
            total_bytes_used=F("total_bytes_used") + delta
        )
    return delta


def close_session(session: Session, reason: str):
    session.is_active = False
    session.logout_time = timezone.now()
    session.logout_reason = reason
    session.save()


def kick_sessions(sessions, reason: str, router=None, actives=None):
    """Kick the given open Sessions off the router and close them. Raises RouterError if the
    router cannot be reached (nothing is closed in that case, so a later sync can retry)."""
    sessions = list(sessions)
    if not sessions:
        return 0
    with use_router(router) as r:
        active_list = actives if actives is not None else r.active_sessions()
        by_id = {a["id"]: a for a in active_list}
        by_user: dict[str, list] = {}
        for a in active_list:
            by_user.setdefault(a["user"], []).append(a)
        for s in sessions:
            username = s.instructor.user.username
            entry = by_id.get(s.mikrotik_session_id)
            if entry is not None and entry["user"] != username:  # RouterOS reuses ids
                entry = None
            entry = entry or next(iter(by_user.get(username, [])), None)
            if entry:
                apply_usage(s, entry["bytes_in"], entry["bytes_out"])
                r.kick(entry["id"], user=username)
            else:
                r.kick_user(username)  # also clears MAC cookies
            close_session(s, reason)
    return len(sessions)


def open_sessions_for_user(user):
    return Session.objects.filter(instructor__user=user, is_active=True).select_related("instructor__user", "semester_account")


# --------------------------------------------------------------------------------------
# captive-portal login
# --------------------------------------------------------------------------------------
@dataclass
class ConnectResult:
    success: bool
    message: str
    code: str = ""
    session: Session | None = None


def _fake_mac_for(ip: str) -> str:
    h = abs(hash(ip)) % (256**4)
    return "02:00:" + ":".join(f"{(h >> s) & 255:02X}" for s in (24, 16, 8, 0))


def resolve_device(router, mac: str, ip: str, peer_ip: str | None) -> tuple[str, str]:
    """Decide which device to log in. The ?mac=&ip= values come from the browser, so they are not trusted:
    on a real router the IP must be the requester's own address and the MAC is what the router's host table
    says for that IP (the posted MAC is used only when the router has no entry)."""
    mac = (mac or "").strip().upper().replace("-", ":")
    ip = (ip or "").strip()
    if peer_ip and (not router.is_mock or not ip):
        ip = peer_ip
    if ip:
        known = (router.host_lookup(ip) or "").upper()
        if MAC_RE.match(known):
            mac = known
    if not MAC_RE.match(mac) and router.is_mock and ip:
        mac = _fake_mac_for(ip)
    return mac, ip


def connect_instructor(user, mac: str, ip: str, peer_ip: str | None = None) -> ConnectResult:
    """Steps 3-4 of the captive-portal flow: validate, mirror to router, log the device in."""
    access = evaluate_access(user)
    if not access.allowed:
        return ConnectResult(False, access.message, access.code)
    account = access.account
    username = user.username
    if not (ip or peer_ip or "").strip():
        return ConnectResult(False, "Could not detect your device address. Reconnect to the Wi-Fi and try again.", "no_ip")
    try:
        with get_router() as r:
            mac, ip = resolve_device(r, mac, ip, peer_ip)
            if not MAC_RE.match(mac):
                return ConnectResult(False, "Could not detect your device. Reconnect to the Wi-Fi and try again.", "no_mac")
            actives = r.active_sessions()
            # Same instructor on another device, or this device still logged in as someone else: drop them first.
            stale = [a for a in actives if a["user"] == username or a["mac"] == mac]
            if stale:
                stale_ids = {a["id"] for a in stale}
                db_open = Session.objects.filter(is_active=True, mikrotik_session_id__in=stale_ids).select_related("instructor__user")
                kick_sessions(db_open, Session.USER, router=r, actives=actives)
                for a in stale:
                    r.kick(a["id"], user=a["user"])
            push_account(r, account, active_users=set(), allowed=access)  # counters reset: nothing active now
            r.login_device(username, account.hotspot_password, mac, ip)
            entry = next((a for a in r.active_sessions() if a["user"] == username), None)
    except RouterError as e:
        audit(None, "login.router_error", username, {"error": str(e)}, actor=user)
        return ConnectResult(
            False, "The network controller is not responding right now. Please try again in a moment.", "router_error"
        )
    with transaction.atomic():
        session = Session.objects.create(
            instructor=user.instructor,
            semester_account=account,
            login_time=timezone.now(),
            mac_address=mac,
            ip_address=ip,
            mikrotik_session_id=entry["id"] if entry else "",
            is_active=True,
        )
    return ConnectResult(True, "Connected.", "ok", session)


def disconnect_user(user, reason=Session.USER, router=None) -> int:
    """Kick all of a user's open sessions (portal logout / disconnect)."""
    return kick_sessions(open_sessions_for_user(user), reason, router=router)


# --------------------------------------------------------------------------------------
# admin-side operations
# --------------------------------------------------------------------------------------
def enforce_user_off(user, reason=Session.ADMIN_KICK, router=None) -> str | None:
    """User was deactivated / account disabled: disable router user + kick sessions.
    Returns a warning string if the router could not be updated."""
    try:
        with use_router(router) as r:
            r.disable_user(user.username)
            sessions = open_sessions_for_user(user)
            if sessions.exists():
                kick_sessions(sessions, reason, router=r)
            else:
                r.kick_user(user.username)
        return None
    except RouterError as e:
        return f"Saved, but the router could not be updated now ({e}). Run 'sync_router' or wait for sync_sessions."


def refresh_user_on_router(user) -> str | None:
    """Mirror the current state of an instructor to the router (enable or disable)."""
    instructor = getattr(user, "instructor", None)
    if instructor is None:
        return None
    account = instructor.current_account
    if account is None:
        return try_router(lambda r: r.disable_user(user.username))
    return try_router(lambda r: push_account(r, account))


def set_active_semester(semester: Semester, request=None) -> list[str]:
    """Activate `semester`; wind down the previous one. Returns a list of warnings."""
    warnings: list[str] = []
    previous = Semester.objects.filter(is_active=True).exclude(pk=semester.pk).first()
    usernames: list[str] = []
    old_sessions = Session.objects.none()
    with transaction.atomic():
        if previous:
            accounts = SemesterAccount.objects.filter(semester=previous, is_active=True)
            usernames = list(accounts.values_list("instructor__user__username", flat=True))
            accounts.update(is_active=False)
            old_sessions = Session.objects.filter(is_active=True, semester_account__semester=previous)
        semester.is_active = True
        semester.save()  # Semester.save() clears any other active semester
    kicked = 0
    if previous:
        try:
            with get_router() as r:
                for name in usernames:
                    r.disable_user(name)
                kicked = kick_sessions(old_sessions.select_related("instructor__user"), Session.SEMESTER_ENDED, router=r)
        except RouterError as e:
            warnings.append(
                f"Semester switched, but the router could not be updated ({e}). "
                "Run 'python manage.py sync_router' once the router is reachable."
            )
    audit(
        request,
        "semester.activated",
        str(semester),
        {"previous": str(previous) if previous else None, "accounts_deactivated": len(usernames), "sessions_kicked": kicked},
    )
    return warnings


def sync_router_full(router=None) -> dict:
    """One-shot: push all policies and active accounts; disable router users Django says are off."""
    summary = {"policies": 0, "users_pushed": 0, "users_disabled": 0, "errors": []}
    with use_router(router) as r:
        for policy in BandwidthPolicy.objects.all():
            ok, msg = sync_policy(policy, r)
            if ok:
                summary["policies"] += 1
            else:
                summary["errors"].append(f"policy {policy.name}: {msg}")
        active_users = {a["user"] for a in r.active_sessions()}
        allowed_names: set[str] = set()
        semester = Semester.get_active()
        if semester:
            accounts = SemesterAccount.objects.filter(semester=semester).select_related(
                "instructor__user", "policy", "semester"
            )
            for acct in accounts:
                res = account_access(acct)
                if res.allowed:
                    allowed_names.add(acct.instructor.user.username)
                try:
                    push_account(r, acct, active_users=active_users, allowed=res)
                    summary["users_pushed" if res.allowed else "users_disabled"] += 1
                except RouterError as e:
                    summary["errors"].append(f"{acct.instructor.user.username}: {e}")
        # router users that Django does not allow
        managed = r.list_managed_users()
        for ru in managed:
            if ru["name"] not in allowed_names and not ru["disabled"]:
                r.disable_user(ru["name"])
                summary["users_disabled"] += 1
        # sessions that should not be online
        managed_names = {u["name"] for u in managed}
        for a in r.active_sessions():
            if a["user"] in managed_names and a["user"] not in allowed_names:
                r.kick(a["id"], user=a["user"])
    return summary
