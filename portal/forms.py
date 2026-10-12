from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import User
from instructors.models import Instructor

SIGNUP_LIMIT_PER_HOUR = 5  # registrations accepted from one device address per hour


class SignupForm(forms.Form):
    """Public self-registration. The account stays 'pending' until an administrator approves it."""

    username = forms.CharField(label="Username", max_length=150, help_text="You will use this to sign in to the Wi-Fi portal.")
    first_name = forms.CharField(max_length=150, label="First name")
    middle_name = forms.CharField(max_length=100, required=False, label="Middle name (optional)")
    last_name = forms.CharField(max_length=150, label="Last name")
    extension_name = forms.CharField(max_length=20, required=False, label="Suffix (optional)", help_text="Jr., III ...")
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = "form-control form-control-lg"
        self.fields["username"].widget.attrs.update(
            {"autocapitalize": "none", "autocorrect": "off", "autocomplete": "username"}
        )

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if " " in username:
            raise ValidationError("The username must not contain spaces.")
        user = User.objects.filter(username__iexact=username).first()
        if user is not None:
            profile = Instructor.objects.filter(user=user).first()
            if profile is not None and profile.approval_status == Instructor.PENDING:
                raise ValidationError("This username is already registered and is waiting for approval.")
            if profile is not None and profile.approval_status == Instructor.REJECTED:
                raise ValidationError("This username was registered but not approved. Please contact the ICT office.")
            raise ValidationError(
                "This username is already taken. Sign in instead, or ask the ICT office if you forgot your password."
            )
        return username

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "The two passwords do not match.")
            else:
                candidate = User(username=data.get("username", ""), first_name=data.get("first_name", ""),
                                 last_name=data.get("last_name", ""))
                try:
                    password_validation.validate_password(p1, candidate)
                except ValidationError as e:
                    self.add_error("password1", e)
        return data

    @transaction.atomic
    def save(self) -> Instructor:
        d = self.cleaned_data
        user = User(
            username=d["username"],
            first_name=d["first_name"],
            middle_name=d["middle_name"],
            last_name=d["last_name"],
            extension_name=d["extension_name"],
            is_instructor=True,
            is_active=True,
        )
        user.set_password(d["password1"])
        user.save()
        return Instructor.objects.create(user=user, approval_status=Instructor.PENDING)
