"""Login rate limiting: LOGIN_MAX_FAILURES failures per (username, ip) -> locked for LOGIN_LOCKOUT_MINUTES."""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import LoginAttempt


def _window_start():
    return timezone.now() - timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)


def _recent_failures(username, ip):
    # Failures since the last success inside the window.
    qs = LoginAttempt.objects.filter(username=username.lower(), ip_address=ip, created_at__gte=_window_start())
    last_success = qs.filter(success=True).order_by("-created_at").first()
    if last_success:
        qs = qs.filter(created_at__gt=last_success.created_at)
    return qs.filter(success=False).order_by("created_at")


def lockout_remaining(username, ip):
    """Seconds left on the lockout, or 0 when not locked."""
    failures = list(_recent_failures(username, ip))
    if len(failures) < settings.LOGIN_MAX_FAILURES:
        return 0
    newest = failures[-1].created_at
    unlock_at = newest + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
    return max(int((unlock_at - timezone.now()).total_seconds()), 0)


def record_attempt(username, ip, success):
    LoginAttempt.objects.create(username=username.lower(), ip_address=ip, success=success)
