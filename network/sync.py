"""Reconcile Django Sessions with what the router reports (used by `manage.py sync_sessions`)."""
from __future__ import annotations

from django.utils import timezone

from accounts.audit import audit
from accounts.models import User
from instructors.models import SemesterAccount

from .models import Session
from .services import (
    AccessResult,
    account_access,
    apply_usage,
    close_session,
    evaluate_access,
    use_router,
)

SYNC_STALE_SECONDS = 120  # sync_sessions runs every 30 s; warn when nothing was recorded for this long

REASON_FOR_CODE = {"quota": Session.QUOTA, "no_semester": Session.SEMESTER_ENDED}


def sync_sessions_once(router=None) -> dict:
    stats = {"updated": 0, "closed": 0, "kicked": 0, "adopted": 0}
    with use_router(router) as r:
        actives = r.active_sessions()
        by_id = {a["id"]: a for a in actives}
        by_user_mac = {(a["user"], a["mac"]): a for a in actives}
        seen_ids: set[str] = set()
        active_users = {a["user"] for a in actives}

        open_sessions = Session.objects.filter(is_active=True).select_related("instructor__user")
        for s in open_sessions:
            username = s.instructor.user.username
            # RouterOS reuses session ids, so an id match only counts if it is the same user's entry
            entry = by_id.get(s.mikrotik_session_id)
            if entry is not None and entry["user"] != username:
                entry = None
            entry = entry or by_user_mac.get((username, s.mac_address.upper()))
            account = (
                SemesterAccount.objects.select_related("instructor__user", "semester", "policy")
                .filter(pk=s.semester_account_id)
                .first()
                if s.semester_account_id
                else None
            )
            if entry is None:
                # Gone from the router: idle timeout, user closed it, or the router enforced the quota.
                # The hotspot user's counters survive the logout (and were reset at this session's login),
                # so they give the final byte count - unless the instructor has already logged in again.
                hit_limit = False
                if username not in active_users:
                    counters = r.user_counters(username)
                    if counters:
                        apply_usage(s, max(counters["bytes_in"], s.bytes_in), max(counters["bytes_out"], s.bytes_out))
                        hit_limit = bool(counters["limit"]) and counters["bytes_in"] + counters["bytes_out"] >= counters["limit"]
                if account is not None:
                    account.refresh_from_db()
                reason = Session.QUOTA if hit_limit or (account and account.quota_exceeded) else Session.IDLE
                close_session(s, reason)
                stats["closed"] += 1
                continue

            seen_ids.add(entry["id"])
            s.mikrotik_session_id = entry["id"]
            apply_usage(s, entry["bytes_in"], entry["bytes_out"])
            s.save()
            stats["updated"] += 1

            if account is not None:
                account.refresh_from_db()  # picks up the F() update
                verdict = account_access(account)
            else:
                verdict = AccessResult(False, "no_account", "no account")
            if not verdict.allowed:
                r.kick(entry["id"], user=username)
                r.disable_user(username)
                reason = REASON_FOR_CODE.get(verdict.code, Session.ADMIN_KICK)
                close_session(s, reason)
                audit(None, "session.auto_kick", username, {"reason": reason, "code": verdict.code}, actor=None)
                stats["kicked"] += 1

        # active on the router but unknown to Django (e.g. MAC-cookie auto login): adopt or remove
        for a in actives:
            if a["id"] in seen_ids:
                continue
            if Session.objects.filter(is_active=True, mikrotik_session_id=a["id"]).exists():
                continue
            user = User.objects.filter(username=a["user"], is_instructor=True).select_related("instructor").first()
            if user is None:
                continue  # not ours - never touch it
            verdict = evaluate_access(user)
            if verdict.allowed:
                s = Session.objects.create(
                    instructor=user.instructor,
                    semester_account=verdict.account,
                    login_time=timezone.now(),
                    mac_address=a["mac"],
                    ip_address=a["address"] or None,
                    mikrotik_session_id=a["id"],
                )
                apply_usage(s, a["bytes_in"], a["bytes_out"])
                s.save()
                stats["adopted"] += 1
            else:
                r.kick(a["id"], user=a["user"])
                stats["kicked"] += 1
    return stats


def run_and_record(router=None) -> dict:
    """sync_sessions_once() plus a heartbeat row so the admin UI can tell whether the loop is alive."""
    from .mikrotik import RouterError
    from .models import SyncState

    state, _ = SyncState.objects.get_or_create(name="sessions")
    try:
        stats = sync_sessions_once(router)
    except RouterError as e:
        state.last_run, state.last_error = timezone.now(), str(e)[:500]
        state.save(update_fields=["last_run", "last_error"])
        raise
    except Exception as e:  # noqa: BLE001
        state.last_run, state.last_error = timezone.now(), f"{type(e).__name__}: {e}"[:500]
        state.save(update_fields=["last_run", "last_error"])
        raise
    now = timezone.now()
    state.last_run = state.last_success = now
    state.last_error = ""
    state.last_stats = stats
    state.save()
    return stats


def sync_health() -> dict:
    """{'running': bool, 'age': seconds|None, 'error': str} for the admin dashboard."""
    from .models import SyncState

    state = SyncState.objects.filter(name="sessions").first()
    if state is None or state.last_run is None:
        return {"running": False, "never": True, "age": None, "error": ""}
    age = int((timezone.now() - state.last_run).total_seconds())
    return {"running": age <= SYNC_STALE_SECONDS, "never": False, "age": age, "error": state.last_error}
