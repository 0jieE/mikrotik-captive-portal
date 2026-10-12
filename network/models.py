from django.db import models


class Session(models.Model):
    USER = "user"
    IDLE = "idle"
    QUOTA = "quota"
    ADMIN_KICK = "admin-kick"
    SEMESTER_ENDED = "semester-ended"
    REASON_CHOICES = [
        (USER, "User logout"),
        (IDLE, "Idle / router timeout"),
        (QUOTA, "Quota used up"),
        (ADMIN_KICK, "Kicked by admin"),
        (SEMESTER_ENDED, "Semester ended"),
    ]

    instructor = models.ForeignKey("instructors.Instructor", on_delete=models.CASCADE, related_name="sessions")
    semester_account = models.ForeignKey(
        "instructors.SemesterAccount", null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions"
    )
    login_time = models.DateTimeField(db_index=True)
    logout_time = models.DateTimeField(null=True, blank=True)
    mac_address = models.CharField(max_length=17, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    consumption = models.FloatField(default=0, help_text="MB (bytes in + out)")
    bytes_in = models.BigIntegerField(default=0)
    bytes_out = models.BigIntegerField(default=0)
    mikrotik_session_id = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    logout_reason = models.CharField(max_length=20, blank=True, choices=REASON_CHOICES)

    class Meta:
        db_table = "session"
        ordering = ["-login_time"]
        indexes = [
            models.Index(fields=["instructor", "is_active"]),
            models.Index(fields=["instructor", "-login_time"]),
        ]

    def __str__(self):
        return f"{self.instructor.user.username} @ {self.login_time:%Y-%m-%d %H:%M}"

    @property
    def total_bytes(self):
        return self.bytes_in + self.bytes_out

    @property
    def duration(self):
        from django.utils import timezone

        end = self.logout_time or timezone.now()
        return end - self.login_time


class SyncState(models.Model):
    """Heartbeat written by `manage.py sync_sessions` so the admin UI can show whether it is running."""

    name = models.CharField(max_length=30, unique=True)
    last_run = models.DateTimeField(null=True, blank=True)
    last_success = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    last_stats = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "sync_state"
