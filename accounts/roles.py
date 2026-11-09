"""Rules that keep at least one person able to run the system. Used by the Django admin (where superusers
create and edit Admin / Superuser accounts)."""
from __future__ import annotations

from .models import User


def _other_active_superusers(target: User):
    return User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk)


def deactivation_block_reason(actor: User, target: User) -> str | None:
    """Nobody can deactivate (or delete) themself, the last active admin account, or the last active superuser."""
    if target.pk == actor.pk:
        return "You cannot deactivate your own account."
    if not target.is_admin or not target.is_active:
        return None
    others = User.objects.filter(is_admin=True, is_active=True).exclude(pk=target.pk)
    if not others.exists():
        return "You cannot deactivate the last active admin account."
    if target.is_superuser and not _other_active_superusers(target).exists():
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


def apply_role(user: User, role: str) -> None:
    """Set the flags for an Admin (portal admin) or Superuser account."""
    user.is_admin = True
    user.is_superuser = user.is_staff = role == User.SUPERUSER  # Admin: never any access to Django admin
