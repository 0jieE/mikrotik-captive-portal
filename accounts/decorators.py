from functools import wraps

from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def _role_required(test, login_url, deny_redirect):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url)
            if not test(request.user):
                if deny_redirect and (request.user.is_admin or request.user.is_instructor):
                    messages.error(request, "You do not have access to that page.")
                    return redirect(deny_redirect(request.user))
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def home_for(user):
    if user.is_admin:
        return "adminpanel:dashboard"
    return "portal:dashboard"


admin_required = _role_required(
    lambda u: u.is_active and u.is_admin, "/accounts/login/", home_for
)
instructor_required = _role_required(
    lambda u: u.is_active and u.is_instructor and not u.is_admin, "/portal/login/", home_for
)


class AdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_active and self.request.user.is_admin


class InstructorRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        u = self.request.user
        return u.is_authenticated and u.is_active and u.is_instructor and not u.is_admin
