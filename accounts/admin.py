from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .audit import audit
from .models import AuditLog, LoginAttempt, User
from .roles import apply_role, deactivation_block_reason, demotion_block_reason

ROLE_CHOICES = [
    (User.STAFF, "Admin (portal admin) - manages the portal only, no Django admin"),
    (User.SUPERUSER, "Superuser - the portal and the Django admin"),
]


class AdminAccountCreationForm(UserCreationForm):
    """Django admin 'Add user': only Admin (portal admin) and Superuser accounts are made here.
    Instructors are added in the admin panel (they also need an instructor profile and an internet account)."""

    role = forms.ChoiceField(choices=ROLE_CHOICES, initial=User.STAFF, widget=forms.RadioSelect)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name")

    def save(self, commit=True):
        user = super().save(commit=False)
        apply_role(user, self.cleaned_data["role"])
        user.must_change_password = True  # the superuser chose this password; the owner picks their own at first sign-in
        if commit:
            user.save()
        return user


class AdminAccountChangeForm(UserChangeForm):
    """Edit an Admin / Superuser account. The role replaces Django's is_staff / is_superuser / is_admin checkboxes."""

    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect)
    actor = None  # the signed-in superuser, set by CdkUserAdmin.get_form

    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].initial = User.SUPERUSER if self.instance.is_superuser else User.STAFF
        self._was_active = self.instance.is_active  # the form overwrites the instance while validating

    def clean(self):
        data = super().clean()
        target, actor = self.instance, self.actor
        if actor is not None:
            if self._was_active and data.get("is_active") is False:
                reason = deactivation_block_reason(actor, User.objects.get(pk=target.pk))
                if reason:
                    self.add_error("is_active", reason)
            if data.get("role"):
                reason = demotion_block_reason(actor, User.objects.get(pk=target.pk), data["role"])
                if reason:
                    self.add_error("role", reason)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        apply_role(user, self.cleaned_data["role"])
        if commit:
            user.save()
        return user


@admin.register(User)
class CdkUserAdmin(UserAdmin):
    add_form = AdminAccountCreationForm
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "description": "Create an <strong>Admin</strong> (portal admin) or a <strong>Superuser</strong>. "
                           "They must choose a new password the first time they sign in. "
                           "Instructors are added in the admin panel, not here.",
            "fields": ("username", "first_name", "last_name", "role", "password1", "password2"),
        }),
    )
    admin_fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "middle_name", "last_name", "extension_name")}),
        ("Role and status", {"fields": ("role", "is_active", "must_change_password")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    instructor_fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "middle_name", "last_name", "extension_name")}),
        ("Status", {"fields": ("is_active", "is_instructor", "must_change_password"),
                    "description": "Instructors are managed in the admin panel (approval, plan, internet account)."}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    list_display = ("username", "last_name", "first_name", "role_display", "is_active")
    list_filter = ("is_superuser", "is_admin", "is_instructor", "is_active")
    search_fields = ("username", "first_name", "last_name")

    @admin.display(description="Role", ordering="is_superuser")
    def role_display(self, obj):
        return obj.role_label or "-"

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return self.add_fieldsets
        return self.admin_fieldsets if obj.is_admin else self.instructor_fieldsets

    def get_form(self, request, obj=None, **kwargs):
        if obj is not None and obj.is_admin:
            kwargs["form"] = type("AdminAccountChangeFormFor", (AdminAccountChangeForm,), {"actor": request.user})
        return super().get_form(request, obj, **kwargs)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)  # bulk delete would skip the "last superuser" checks below
        return actions

    def has_delete_permission(self, request, obj=None):
        if obj is not None and deactivation_block_reason(request.user, obj):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.is_admin:
            if change:
                audit(request, "admin.updated", obj.username, {"fields": sorted(form.changed_data), "role": obj.role})
            else:
                audit(request, "admin.created", obj.username, {"role": obj.role})

    def delete_model(self, request, obj):
        audit(request, "admin.deleted" if obj.is_admin else "user.deleted", obj.username, {"role": obj.role})
        super().delete_model(request, obj)


admin.site.register(AuditLog)
admin.site.register(LoginAttempt)


# Django's built-in admin is for superusers only; day-to-day work happens in /admin-panel/.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser
