from datetime import timedelta

from django import template

register = template.Library()


@register.filter
def hms(value):
    """Seconds (or a timedelta) -> '1h 05m 03s'."""
    if isinstance(value, timedelta):
        value = value.total_seconds()
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "-"
    h, rem = divmod(value, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


@register.filter
def human_bytes(value):
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


@register.filter
def mb(value):
    try:
        return f"{float(value or 0):,.1f}"
    except (TypeError, ValueError):
        return "-"
