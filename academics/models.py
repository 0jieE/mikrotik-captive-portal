from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.text import slugify


class SchoolYear(models.Model):
    sy = models.CharField("School year", max_length=9, unique=True, help_text="e.g. 2026-2027")

    class Meta:
        db_table = "school_year"
        ordering = ["-sy"]

    def __str__(self):
        return self.sy

    def clean(self):
        parts = self.sy.split("-")
        if len(parts) != 2 or not all(p.isdigit() and len(p) == 4 for p in parts) or int(parts[1]) != int(parts[0]) + 1:
            raise ValidationError({"sy": "Use the format YYYY-YYYY with consecutive years, e.g. 2026-2027."})


class Semester(models.Model):
    FIRST = "1st"
    SECOND = "2nd"
    SUMMER = "Summer"
    SEMESTER_CHOICES = [(FIRST, "1st Semester"), (SECOND, "2nd Semester"), (SUMMER, "Summer")]

    semester = models.CharField(max_length=10, choices=SEMESTER_CHOICES)
    sy = models.ForeignKey(SchoolYear, on_delete=models.PROTECT, related_name="semesters")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        db_table = "semester"
        ordering = ["-sy__sy", "-start_date"]
        constraints = [models.UniqueConstraint(fields=["semester", "sy"], name="uniq_semester_per_sy")]

    def __str__(self):
        return f"{self.get_semester_display()} {self.sy}"

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before the start date."})

    def save(self, *args, **kwargs):
        """Enforce: only ONE semester may be active at a time."""
        with transaction.atomic():
            if self.is_active:
                Semester.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)
            super().save(*args, **kwargs)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).select_related("sy").first()


class BandwidthPolicy(models.Model):
    SYNC_NEVER = "never"
    SYNC_OK = "ok"
    SYNC_FAILED = "failed"

    name = models.CharField(max_length=60, unique=True)
    max_speed_down_mbps = models.FloatField("Download (Mbps)")
    max_speed_up_mbps = models.FloatField("Upload (Mbps)")
    quota_limit_mb = models.FloatField("Quota (MB)", null=True, blank=True, help_text="Leave blank for unlimited.")
    session_timeout_minutes = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = no timeout.")
    is_default = models.BooleanField(default=False)
    mikrotik_profile_name = models.CharField(max_length=80, unique=True, blank=True, editable=False)
    sync_status = models.CharField(max_length=10, default=SYNC_NEVER, editable=False)
    sync_message = models.CharField(max_length=255, blank=True, editable=False)
    last_synced_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        db_table = "bandwidth_policy"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.max_speed_down_mbps is not None and self.max_speed_down_mbps <= 0:
            raise ValidationError({"max_speed_down_mbps": "Must be greater than zero."})
        if self.max_speed_up_mbps is not None and self.max_speed_up_mbps <= 0:
            raise ValidationError({"max_speed_up_mbps": "Must be greater than zero."})
        if self.quota_limit_mb is not None and self.quota_limit_mb <= 0:
            raise ValidationError({"quota_limit_mb": "Must be greater than zero (or blank for unlimited)."})

    def _generate_profile_name(self):
        base = f"cdk-{slugify(self.name) or 'policy'}"
        candidate, n = base, 2
        while BandwidthPolicy.objects.exclude(pk=self.pk).filter(mikrotik_profile_name=candidate).exists():
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if not self.mikrotik_profile_name:
                self.mikrotik_profile_name = self._generate_profile_name()
            if self.is_default:
                BandwidthPolicy.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)
            super().save(*args, **kwargs)

    @property
    def quota_bytes(self):
        if self.quota_limit_mb is None:
            return None
        return int(self.quota_limit_mb * 1024 * 1024)

    @property
    def rate_limit(self):
        """RouterOS rate-limit string: rx/tx from the router's side = upload/download."""
        return f"{_rate(self.max_speed_up_mbps)}/{_rate(self.max_speed_down_mbps)}"

    @property
    def session_timeout(self):
        if not self.session_timeout_minutes:
            return ""
        h, m = divmod(self.session_timeout_minutes, 60)
        return (f"{h}h" if h else "") + (f"{m}m" if m else "")

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_default=True).first() or cls.objects.order_by("id").first()


def _rate(mbps: float) -> str:
    if float(mbps).is_integer():
        return f"{int(mbps)}M"
    return f"{int(round(mbps * 1000))}k"
