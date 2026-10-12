from django.shortcuts import redirect


class ForcePasswordChangeMiddleware:
    """Users flagged must_change_password can only reach the change-password page and logout."""

    ALLOWED_PREFIXES = ("/accounts/password/", "/accounts/logout/", "/portal/logout/", "/static/", "/admin/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.must_change_password:
            if not request.path.startswith(self.ALLOWED_PREFIXES):
                return redirect("/accounts/password/")
        return self.get_response(request)
