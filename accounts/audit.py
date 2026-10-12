from .models import AuditLog


def client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def audit(request, action, target="", details=None, actor=None):
    """Write an AuditLog row. `request` may be None (management commands)."""
    if actor is None and request is not None and getattr(request, "user", None) is not None:
        if request.user.is_authenticated:
            actor = request.user
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target=str(target)[:200],
        details=details or {},
        ip_address=client_ip(request),
    )
