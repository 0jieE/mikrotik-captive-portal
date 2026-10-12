from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.audit import audit
from accounts.decorators import superuser_required
from accounts.models import User

from .forms import AdminAccountForm


def _other_active_superusers(target: User):
    return User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk)


def deactivation_block_reason(actor: User, target: User) -> str | None:
    """Nobody can deactivate themself, the last active account, or the last active superuser."""
    if target.pk == actor.pk:
        return "You cannot deactivate your own account."
    others = User.objects.filter(is_admin=True, is_active=True).exclude(pk=target.pk)
    if target.is_active and not others.exists():
        return "You cannot deactivate the last active account."
    if target.is_active and target.is_superuser and not _other_active_superusers(target).exists():
        return "You cannot deactivate the last active superuser."
    return None


def demotion_block_reason(actor: User, target: User, new_role: str) -> str | None:
    """Taking the superuser role away from yourself, or from the last active superuser, would lock everyone out of Django admin."""
    if not target.is_superuser or new_role == User.SUPERUSER:
        return None
    if target.pk == actor.pk:
        return "You cannot remove your own superuser role. Ask another superuser to do it."
    if target.is_active and not _other_active_superusers(target).exists():
        return "There must always be at least one active superuser."
    return None


@superuser_required
def admin_list(request):
    return render(request, "adminpanel/admins/list.html", {"admins": User.objects.filter(is_admin=True).order_by("-is_superuser", "username")})


@superuser_required
def admin_add(request):
    form = AdminAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        audit(request, "admin.created", user.username, {"role": user.role})
        messages.success(request, f"{user.role_label} account {user.username} added.")
        return redirect("adminpanel:admin_list")
    return render(request, "adminpanel/admins/form.html", {"form": form, "title": "Add staff or superuser"})


@superuser_required
def admin_edit(request, pk):
    target = get_object_or_404(User, pk=pk, is_admin=True)
    was_active = target.is_active
    form = AdminAccountForm(request.POST or None, instance=target)
    if request.method == "POST" and form.is_valid():
        blocked = False
        if was_active and not form.cleaned_data["is_active"]:
            reason = deactivation_block_reason(request.user, target)
            if reason:
                form.add_error("is_active", reason)
                blocked = True
        reason = demotion_block_reason(request.user, target, form.cleaned_data["role"])
        if reason:
            form.add_error("role", reason)
            blocked = True
        if blocked:
            return render(request, "adminpanel/admins/form.html", {"form": form, "title": f"Edit {target.username}"})
        user = form.save()
        audit(request, "admin.updated", user.username, {"fields": sorted(form.changed_data), "role": user.role})
        messages.success(request, "Account saved.")
        return redirect("adminpanel:admin_list")
    return render(request, "adminpanel/admins/form.html", {"form": form, "title": f"Edit {target.username}"})


@superuser_required
@require_POST
def admin_toggle_active(request, pk):
    target = get_object_or_404(User, pk=pk, is_admin=True)
    if target.is_active:
        reason = deactivation_block_reason(request.user, target)
        if reason:
            messages.error(request, reason)
            return redirect("adminpanel:admin_list")
    target.is_active = not target.is_active
    target.save(update_fields=["is_active", "updated_at"])
    audit(request, "admin.activated" if target.is_active else "admin.deactivated", target.username)
    messages.success(request, f"{target.username} {'activated' if target.is_active else 'deactivated'}.")
    return redirect("adminpanel:admin_list")
