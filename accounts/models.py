from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models


class UserManager(DjangoUserManager):
    """This system stores no e-mail addresses, so create_user()/create_superuser() simply ignore `email`."""

    def _create_user(self, username, email=None, password=None, **extra_fields):
        if not username:
            raise ValueError("The given username must be set")
        user = self.model(username=self.model.normalize_username(username), **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user


class User(AbstractUser):
    """Custom user. Instructors and admins share this table (see is_admin / is_instructor)."""

    email = None  # no e-mail addresses are collected or stored
    REQUIRED_FIELDS = []
    objects = UserManager()

    def clean(self):
        # AbstractUser.clean() normalises self.email, which no longer exists
        super(AbstractUser, self).clean()

    middle_name = models.CharField(max_length=100, blank=True)
    extension_name = models.CharField(max_length=20, blank=True, help_text="Jr., III, ...")
    is_admin = models.BooleanField(default=False)
    is_instructor = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user"
        ordering = ["last_name", "first_name"]

    def get_full_name(self):
        """'Last, First M. Ext' style used across the UI."""
        parts = f"{self.last_name}, {self.first_name}".strip(", ")
        if self.middle_name:
            parts += f" {self.middle_name[0]}."
        if self.extension_name:
            parts += f" {self.extension_name}"
        return parts or self.username

    @property
    def display_name(self):
        return self.get_full_name()


class AuditLog(models.Model):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries")
    action = models.CharField(max_length=60, db_index=True)
    target = models.CharField(max_length=200, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_log"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.target}"


class LoginAttempt(models.Model):
    """Used for login rate limiting (per username + IP)."""

    username = models.CharField(max_length=150, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    success = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "login_attempt"
        indexes = [models.Index(fields=["username", "ip_address", "created_at"])]
