from .approval import pending_count


def pending_registrations(request):
    """Number of self-registrations waiting for approval; only looked up for signed-in administrators."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not getattr(user, "is_admin", False):
        return {}
    return {"pending_registrations": pending_count()}
