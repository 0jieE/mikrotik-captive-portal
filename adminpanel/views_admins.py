from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.audit import audit
from accounts.decorators import admin_required
from accounts.models import User

from .forms import AdminAccountForm


def deactivation_block_reason(actor: User, target: User) -> str | None:
    """An admin cannot deactivate themself or the last active admin."""
    if target.pk == actor.pk:
        return "You cannot deactivate your own account."
    others = User.objects.filter(is_admin=True, is_active=True).exclude(pk=target.pk)
    if target.is_active and not others.exists():
        return "You cannot deactivate the last active administrator."
    return None


@admin_required
def admin_list(request):
    return render(request, "adminpanel/admins/list.html", {"admins": User.objects.filter(is_admin=True).order_by("username")})


@admin_required
def admin_add(request):
    form = AdminAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        audit(request, "admin.created", user.username)
        messages.success(request, f"Administrator {user.username} added.")
        return redirect("adminpanel:admin_list")
    return render(request, "adminpanel/admins/form.html", {"form": form, "title": "Add administrator"})


@admin_required
def admin_edit(request, pk):
    target = get_object_or_404(User, pk=pk, is_admin=True)
    was_active = target.is_active
    form = AdminAccountForm(request.POST or None, instance=target)
    if request.method == "POST" and form.is_valid():
        if was_active and not form.cleaned_data["is_active"]:
            reason = deactivation_block_reason(request.user, target)
            if reason:
                form.add_error("is_active", reason)
                return render(request, "adminpanel/admins/form.html", {"form": form, "title": f"Edit {target.username}"})
        user = form.save()
        audit(request, "admin.updated", user.username, {"fields": sorted(form.changed_data)})
        messages.success(request, "Administrator saved.")
        return redirect("adminpanel:admin_list")
    return render(request, "adminpanel/admins/form.html", {"form": form, "title": f"Edit {target.username}"})


@admin_required
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
