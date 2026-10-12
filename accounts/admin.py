from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, LoginAttempt, User


@admin.register(User)
class CdkUserAdmin(UserAdmin):
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "middle_name", "last_name", "extension_name")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("CDK", {"fields": ("is_admin", "is_instructor", "must_change_password")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    list_display = ("username", "last_name", "first_name", "is_admin", "is_instructor", "is_active")
    list_filter = ("is_admin", "is_instructor", "is_active")
    search_fields = ("username", "first_name", "last_name")


admin.site.register(AuditLog)
admin.site.register(LoginAttempt)


# Django's built-in admin is for superusers only; day-to-day work happens in /admin-panel/.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser
