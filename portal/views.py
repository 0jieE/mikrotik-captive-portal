from datetime import timedelta
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.audit import audit, client_ip
from accounts.decorators import instructor_required
from accounts.models import AuditLog, User
from accounts.ratelimit import lockout_remaining, record_attempt
from network.mikrotik import RouterError
from network.models import Session
from network.services import connect_instructor, disconnect_user, evaluate_access
from instructors.models import MB, Instructor

from .forms import SIGNUP_LIMIT_PER_HOUR, SignupForm


# Hosts phones/PCs use to detect a captive portal. Sending an instructor there after login would make the
# OS close the sign-in window at once, so they land on their dashboard instead.
PROBE_HOSTS = (
    "captive.apple.com", "connectivitycheck.gstatic.com", "connectivitycheck.android.com", "clients3.google.com",
    "www.msftconnecttest.com", "www.msftncsi.com", "detectportal.firefox.com", "play.googleapis.com",
    "network-test.debian.org", "nmcheck.gnome.org", "neverssl.com",
)
# Denials where the credentials are right but internet is not allowed: let the instructor see the dashboard
# (usage, quota, plan, history) instead of a dead-end login error.
DASHBOARD_CODES = {"quota", "no_semester", "no_account", "account_disabled", "pending_approval"}


def _safe_dst(dst: str) -> str:
    """Only http(s) URLs or local paths may be used as the post-login destination."""
    if not dst:
        return ""
    parsed = urlparse(dst)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        if (parsed.hostname or "").lower() in PROBE_HOSTS:
            return ""
        return dst
    if not parsed.scheme and not parsed.netloc and dst.startswith("/") and not dst.startswith("//"):
        return dst
    return ""


def login_view(request):
    """Captive-portal login. The router's login.html sends instructors here with ?mac=&ip=&dst=."""
    src = request.POST if request.method == "POST" else request.GET
    mac = src.get("mac", "")
    ip = src.get("ip", "") or client_ip(request) or ""
    dst = _safe_dst(src.get("dst", ""))
    ctx = {"mac": mac, "ip": ip, "dst": dst, "username": "", "error": None, "error_code": ""}

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        ctx["username"] = username
        who = client_ip(request)  # rate limit on the real peer address, not on the spoofable ?ip=
        wait = lockout_remaining(username, who)
        if wait:
            ctx["error"] = f"Too many failed attempts. Please wait {wait // 60 + 1} minute(s) and try again."
            ctx["error_code"] = "locked"
            audit(request, "login.locked", username, {"portal": "instructor"})
            return render(request, "portal/login.html", ctx, status=429)

        # exact match first; phone keyboards often capitalise the first letter, so fall back to a case-insensitive match
        user = User.objects.filter(username=username).first() or User.objects.filter(username__iexact=username).first()
        if user is None or not user.check_password(password):
            record_attempt(username, who, False)
            audit(request, "login.failed", username, {"portal": "instructor", "reason": "bad_credentials"})
            ctx["error"] = "Wrong username or password."
            ctx["error_code"] = "bad_credentials"
            return render(request, "portal/login.html", ctx)

        record_attempt(username, who, True)  # credentials were right
        if user.is_admin:
            ctx["error"] = "Administrators sign in through the staff login page, not the Wi-Fi portal."
            ctx["error_code"] = "not_instructor"
            audit(request, "login.denied", username, {"portal": "instructor", "reason": "admin_account"}, actor=user)
            return render(request, "portal/login.html", ctx)

        result = connect_instructor(user, mac, ip, peer_ip=client_ip(request))
        if not result.success:
            audit(request, "login.denied", username, {"portal": "instructor", "reason": result.code, "ip": ip, "mac": mac}, actor=user)
            if result.code in DASHBOARD_CODES:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                messages.warning(request, result.message)
                return redirect("accounts:change_password" if user.must_change_password else "portal:dashboard")
            ctx["error"] = result.message
            ctx["error_code"] = result.code
            return render(request, "portal/login.html", ctx)

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        audit(request, "login.success", username, {"portal": "instructor", "ip": ip, "mac": result.session.mac_address}, actor=user)
        if user.must_change_password:
            messages.info(request, "You are connected. Please set a new password now.")
            return redirect("accounts:change_password")
        return redirect(dst or "portal:dashboard")

    return render(request, "portal/login.html", ctx)


def signup(request):
    """Public self-registration for instructors. The account needs administrator approval before it can connect."""
    if request.user.is_authenticated and request.user.is_instructor:
        return redirect("portal:dashboard")
    ip = client_ip(request)
    recent = AuditLog.objects.filter(
        action="instructor.signup", ip_address=ip, created_at__gte=timezone.now() - timedelta(hours=1)
    ).count()
    form = SignupForm(request.POST or None)
    status = 200
    if request.method == "POST":
        if recent >= SIGNUP_LIMIT_PER_HOUR:
            form.add_error(None, "Too many registrations from this device. Please try again later or visit the ICT office.")
            status = 429
        elif form.is_valid():
            instructor = form.save()
            audit(request, "instructor.signup", instructor.user.username,
                  {"name": instructor.user.get_full_name()}, actor=None)
            return redirect("portal:signup_done")
    return render(request, "portal/signup.html", {"form": form}, status=status)


def signup_done(request):
    return render(request, "portal/signup_done.html")


def _dashboard_context(user):
    instructor = user.instructor
    account = instructor.current_account
    session = Session.objects.filter(instructor=instructor, is_active=True).first()
    access = evaluate_access(user)
    last_closed = None
    if session is None:
        last_closed = Session.objects.filter(instructor=instructor, is_active=False, logout_time__isnull=False).order_by("-logout_time").first()
    return {
        "last_closed": last_closed,
        "instructor": instructor,
        "account": account,
        "policy": account.policy if account else None,
        "session": session,
        "session_seconds": int((timezone.now() - session.login_time).total_seconds()) if session else 0,
        "access": access,
        "used_mb": round(account.total_bytes_used / MB, 1) if account else 0,
        "quota_mb": account.policy.quota_limit_mb if account else None,
    }


@instructor_required
def dashboard(request):
    return render(request, "portal/dashboard.html", _dashboard_context(request.user))


@instructor_required
def dashboard_live(request):
    return render(request, "portal/partials/status.html", _dashboard_context(request.user))


@instructor_required
@require_POST
def connect(request):
    """'Connect this device' from the dashboard (the instructor is already signed in to the portal)."""
    result = connect_instructor(request.user, "", "", peer_ip=client_ip(request))
    if result.success:
        audit(request, "login.success", request.user.username, {"portal": "instructor", "via": "dashboard"})
        messages.success(request, "Connected. You can now use the internet.")
    else:
        audit(request, "login.denied", request.user.username, {"portal": "instructor", "reason": result.code, "via": "dashboard"})
        messages.error(request, result.message)
    return redirect("portal:dashboard")


@instructor_required
def history(request):
    sessions = Session.objects.filter(instructor=request.user.instructor, is_active=False).select_related("semester_account__semester")[:300]
    return render(request, "portal/history.html", {"sessions": sessions})


@instructor_required
@require_POST
def disconnect(request):
    try:
        n = disconnect_user(request.user)
        audit(request, "session.disconnect", request.user.username, {"sessions": n})
        messages.success(request, "You have been disconnected from the Wi-Fi." if n else "You were not connected.")
    except RouterError:
        messages.error(request, "The network controller is not responding. Please try again.")
    return redirect("portal:dashboard")


@require_POST
def logout_view(request):
    user = request.user
    if user.is_authenticated and user.is_instructor:
        try:
            disconnect_user(user)
        except RouterError:
            messages.warning(request, "Signed out, but the network controller could not be reached to disconnect your device.")
        audit(request, "logout", user.username)
    logout(request)
    return redirect("portal:login")
