import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

MB = 1024 * 1024


def generate_hotspot_password() -> str:
    return secrets.token_urlsafe(18)


class Instructor(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPROVAL_CHOICES = [(PENDING, "Pending approval"), (APPROVED, "Approved"), (REJECTED, "Rejected")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="instructor")
    # Admin-created and imported instructors are approved at once; self-registered ones start as pending.
    approval_status = models.CharField(max_length=10, choices=APPROVAL_CHOICES, default=APPROVED, db_index=True)
    registered_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "instructor"
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.username} - {self.user.get_full_name()}"

    @property
    def username(self):
        return self.user.username

    @property
    def is_approved(self):
        return self.approval_status == self.APPROVED

    @property
    def current_account(self):
        from academics.models import Semester

        sem = Semester.get_active()
        if not sem:
            return None
        return self.accounts.filter(semester=sem).select_related("policy", "semester").first()


class SemesterAccount(models.Model):
    """One Instructor -> many SemesterAccounts (one per semester)."""

    instructor = models.ForeignKey(Instructor, on_delete=models.CASCADE, related_name="accounts")
    semester = models.ForeignKey("academics.Semester", on_delete=models.PROTECT, related_name="accounts")
    policy = models.ForeignKey("academics.BandwidthPolicy", on_delete=models.PROTECT, related_name="accounts")
    is_active = models.BooleanField(default=True)
    hotspot_password = models.CharField(max_length=64, default=generate_hotspot_password, editable=False)
    total_bytes_used = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "semester_account"
        constraints = [models.UniqueConstraint(fields=["instructor", "semester"], name="uniq_account_per_semester")]
        ordering = ["-semester__start_date"]

    def __str__(self):
        return f"{self.instructor.user.username} / {self.semester}"

    @property
    def quota_bytes(self):
        return self.policy.quota_bytes

    @property
    def remaining_bytes(self):
        """None = unlimited."""
        q = self.quota_bytes
        if q is None:
            return None
        return max(q - self.total_bytes_used, 0)

    @property
    def quota_exceeded(self):
        q = self.quota_bytes
        return q is not None and self.total_bytes_used >= q

    @property
    def used_mb(self):
        return round(self.total_bytes_used / MB, 2)

    @property
    def quota_percent(self):
        q = self.quota_bytes
        if not q:
            return 0
        return min(100, round(self.total_bytes_used * 100 / q))
