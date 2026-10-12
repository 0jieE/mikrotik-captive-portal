from django.utils.functional import SimpleLazyObject

from .models import Semester


def active_semester(request):
    """Makes `active_semester` available to every template (looked up lazily, only if a template uses it)."""
    return {"active_semester": SimpleLazyObject(Semester.get_active)}
