from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .audit import audit, client_ip
from .decorators import home_for
from .ratelimit import lockout_remaining, record_attempt


def home(request):
    if not request.user.is_authenticated:
        return redirect("portal:login")
    return redirect(home_for(request.user))


def staff_login(request):
    """Login page for administrators."""
    if request.user.is_authenticated and request.user.is_admin:
        return redirect("adminpanel:dashboard")
    ip = client_ip(request)
    error = None
    username = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        wait = lockout_remaining(username, ip)
        if wait:
            error = f"Too many failed attempts. Try again in {wait // 60 + 1} minute(s)."
            audit(request, "login.locked", username, {"portal": "admin"}, actor=None)
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None and user.is_active and user.is_admin:
                record_attempt(username, ip, True)
                login(request, user)
                audit(request, "login.success", username, {"portal": "admin"})
                return redirect(request.GET.get("next") or "adminpanel:dashboard")
            record_attempt(username, ip, False)
            audit(request, "login.failed", username, {"portal": "admin"}, actor=None)
            error = "Invalid administrator username or password."
    return render(request, "accounts/staff_login.html", {"error": error, "username": username})


@require_POST
def logout_view(request):
    audit(request, "logout", request.user.get_username() if request.user.is_authenticated else "")
    logout(request)
    return redirect("accounts:staff_login")


@login_required
def change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs["class"] = "form-control"
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.must_change_password = False
        user.save(update_fields=["must_change_password", "updated_at"])
        update_session_auth_hash(request, user)
        audit(request, "password.changed", user.get_username())
        messages.success(request, "Your password was changed.")
        return redirect(home_for(user))
    base = "base_admin.html" if request.user.is_admin else "base_portal.html"
    return render(
        request,
        "accounts/change_password.html",
        {"form": form, "base_template": base, "forced": request.user.must_change_password},
    )
