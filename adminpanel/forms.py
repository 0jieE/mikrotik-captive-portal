from django import forms
from django.db import transaction

from academics.models import BandwidthPolicy, SchoolYear, Semester
from accounts.models import User
from instructors.masterlist import generate_password
from instructors.models import SemesterAccount, Instructor


class BootstrapMixin:
    def _style(self):
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs["class"] = "form-check-input"
            elif isinstance(w, forms.Select):
                w.attrs["class"] = "form-select"
            else:
                w.attrs["class"] = "form-control"


class InstructorForm(BootstrapMixin, forms.Form):
    """Create / edit an instructor: User fields + Instructor fields (+ policy on create)."""

    username = forms.CharField(max_length=150, help_text="The instructor signs in to the Wi-Fi portal with this.")
    first_name = forms.CharField(max_length=150)
    middle_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=150)
    extension_name = forms.CharField(max_length=20, required=False, help_text="Jr., III ...")
    is_active = forms.BooleanField(required=False, initial=True, label="Account active")
    policy = forms.ModelChoiceField(
        BandwidthPolicy.objects.all(), required=False, empty_label="(default policy)", label="Bandwidth policy"
    )
    password = forms.CharField(
        required=False, label="Temporary password", help_text="Leave blank to generate one (shown once after saving)."
    )

    def __init__(self, *args, instructor: Instructor | None = None, **kwargs):
        self.instructor = instructor
        if instructor is not None:
            kwargs.setdefault(
                "initial",
                {
                    "username": instructor.user.username,
                    "first_name": instructor.user.first_name,
                    "middle_name": instructor.user.middle_name,
                    "last_name": instructor.user.last_name,
                    "extension_name": instructor.user.extension_name,
                    "is_active": instructor.user.is_active,
                },
            )
        super().__init__(*args, **kwargs)
        if instructor is not None:
            # policy / password are handled by dedicated actions when editing
            del self.fields["password"]
            del self.fields["policy"]
        self._style()

    def clean(self):
        data = super().clean()
        username = (data.get("username") or "").strip()
        data["username"] = username
        if " " in username:
            self.add_error("username", "The username must not contain spaces.")
        users = User.objects.filter(username__iexact=username)
        if self.instructor is not None:
            users = users.exclude(pk=self.instructor.user_id)
        if username and users.exists():
            self.add_error("username", "This username is already taken.")
        return data

    @transaction.atomic
    def save(self):
        d = self.cleaned_data
        generated = None
        if self.instructor is None:
            user = User(username=d["username"], is_instructor=True)
            password = d.get("password") or generate_password()
            generated = password
            user.set_password(password)
            user.must_change_password = True
        else:
            user = self.instructor.user
        user.first_name, user.middle_name = d["first_name"], d["middle_name"]
        user.last_name, user.extension_name = d["last_name"], d["extension_name"]
        user.username = d["username"]
        user.is_active = d["is_active"]
        user.save()
        instructor = self.instructor or Instructor(user=user)
        instructor.save()
        if self.instructor is None:
            semester = Semester.get_active()
            policy = d.get("policy") or BandwidthPolicy.get_default()
            if semester and policy:
                SemesterAccount.objects.get_or_create(instructor=instructor, semester=semester, defaults={"policy": policy})
        return instructor, generated


class AdminAccountForm(BootstrapMixin, forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False), help_text="Required when adding an admin.")

    class Meta:
        model = User
        fields = ["username", "first_name", "middle_name", "last_name", "extension_name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        if self.instance.pk:
            self.fields["password"].label = "New password"
            self.fields["password"].help_text = "Leave blank to keep the current password."

    def clean_password(self):
        pw = self.cleaned_data.get("password", "")
        if not self.instance.pk and not pw:
            raise forms.ValidationError("A password is required for new admin accounts.")
        return pw

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_admin = True
        pw = self.cleaned_data.get("password")
        if pw:
            user.set_password(pw)
            user.must_change_password = not self.instance.pk or user.must_change_password
        if commit:
            user.save()
        return user


class SchoolYearForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = SchoolYear
        fields = ["sy"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class SemesterForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Semester
        fields = ["sy", "semester", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields["sy"].label = "School year"


class PolicyForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = BandwidthPolicy
        fields = ["name", "max_speed_down_mbps", "max_speed_up_mbps", "quota_limit_mb", "session_timeout_minutes", "is_default"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class ResetPasswordForm(BootstrapMixin, forms.Form):
    password = forms.CharField(required=False, label="Temporary password", help_text="Leave blank to generate one.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class MasterlistUploadForm(BootstrapMixin, forms.Form):
    file = forms.FileField(label="Masterlist file (.csv or .xlsx)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
