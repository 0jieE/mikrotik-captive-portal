import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from academics.models import BandwidthPolicy, SchoolYear, Semester
from accounts.models import User
from network.models import Session
from instructors.models import MB, SemesterAccount, Instructor

SAMPLES = [
    # (username, first, middle, last, extension)
    ("INS-0001", "Roberto", "Santos", "Dela Cruz", ""),
    ("INS-0002", "Maricel", "Lopez", "Reyes", ""),
    ("INS-0003", "Jose", "Garcia", "Ramos", "Jr."),
    ("INS-0004", "Ana", "Bautista", "Flores", ""),
    ("INS-0005", "Pedro", "Mendoza", "Aquino", ""),
    ("INS-0006", "Liza", "Cruz", "Villanueva", ""),
    ("INS-0007", "Mark", "Torres", "Castillo", "III"),
    ("INS-0008", "Grace", "Navarro", "Pascual", ""),
    ("INS-0009", "Carlo", "Domingo", "Salazar", ""),
    ("INS-0010", "Rhea", "Aguilar", "Manalo", ""),
]
# a self-registration still waiting for approval (shows the approval flow in the admin panel)
PENDING_SAMPLE = ("INS-0011", "Nina", "Dela", "Cruz", "")
SAMPLE_PASSWORD = "Instructor@123"
STAFF_PASSWORD = "Staff@12345"


class Command(BaseCommand):
    help = "Create demo data: school year, active semester, policies, admin, test instructor and 10 sample instructors."

    @transaction.atomic
    def handle(self, *args, **opts):
        rnd = random.Random(2026)
        today = timezone.localdate()

        sy, _ = SchoolYear.objects.get_or_create(sy="2026-2027")
        sem, created = Semester.objects.get_or_create(
            semester=Semester.FIRST,
            sy=sy,
            defaults={"start_date": today - timedelta(days=30), "end_date": today + timedelta(days=120)},
        )
        if not Semester.objects.filter(is_active=True).exists():
            sem.is_active = True
            sem.save()

        standard, _ = BandwidthPolicy.objects.update_or_create(
            name="Standard",
            defaults=dict(max_speed_down_mbps=2, max_speed_up_mbps=2, quota_limit_mb=2048, is_default=True),
        )
        restricted, _ = BandwidthPolicy.objects.update_or_create(
            name="Restricted",
            defaults=dict(max_speed_down_mbps=1, max_speed_up_mbps=1, quota_limit_mb=500),
        )

        admin, made = User.objects.get_or_create(
            username="admin",
            defaults=dict(first_name="System", last_name="Administrator", is_admin=True, is_staff=True, is_superuser=True),
        )
        admin.is_admin = admin.is_staff = admin.is_superuser = True
        admin.set_password(settings.SEED_ADMIN_PASSWORD)
        admin.save()

        # a STAFF account (admin panel only, no Django admin) to try the two roles side by side
        staff, _ = User.objects.get_or_create(username="staff", defaults=dict(first_name="Demo", last_name="Staff"))
        staff.is_admin, staff.is_staff, staff.is_superuser, staff.is_instructor, staff.is_active = True, False, False, False, True
        staff.set_password(STAFF_PASSWORD)
        staff.save()

        self._instructor("test", "test123", "Test", "Instructor", "", standard, sem)

        for username, first, middle, last, ext in SAMPLES:
            instructor = self._instructor(username, SAMPLE_PASSWORD, first, last, middle, standard, sem, ext=ext)
            if username in ("INS-0009", "INS-0010"):
                acct = instructor.accounts.get(semester=sem)
                acct.policy = restricted
                acct.save()
            if not instructor.sessions.exists():
                self._history(instructor, rnd, today)

        username, first, middle, last, ext = PENDING_SAMPLE
        puser, _ = User.objects.get_or_create(username=username)
        puser.first_name, puser.middle_name, puser.last_name, puser.extension_name = first, middle, last, ext
        puser.is_instructor, puser.is_admin, puser.is_active = True, False, True
        puser.set_password(SAMPLE_PASSWORD)
        puser.save()
        Instructor.objects.update_or_create(user=puser, defaults=dict(approval_status=Instructor.PENDING))

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write("  Superuser:    admin / (SEED_ADMIN_PASSWORD from .env)   - admin panel + Django admin")
        self.stdout.write(f"  Staff:        staff / {STAFF_PASSWORD}   - admin panel only")
        self.stdout.write("  Test instructor: test / test123")
        self.stdout.write(f"  Sample instructors: INS-0001 .. INS-0010 / {SAMPLE_PASSWORD}")
        self.stdout.write(f"  Pending registration (needs approval): {PENDING_SAMPLE[0]} / {SAMPLE_PASSWORD}")

    def _instructor(self, username, password, first, last, middle, policy, sem, ext=""):
        user, _ = User.objects.get_or_create(username=username)
        user.first_name, user.last_name, user.middle_name, user.extension_name = first, last, middle, ext
        user.is_instructor, user.is_admin, user.is_active = True, False, True
        user.set_password(password)
        user.save()
        instructor, _ = Instructor.objects.get_or_create(user=user)
        SemesterAccount.objects.get_or_create(instructor=instructor, semester=sem, defaults=dict(policy=policy))
        return instructor

    def _history(self, instructor, rnd, today):
        account = instructor.accounts.first()
        total = 0
        quota = account.policy.quota_limit_mb or 1000
        for _ in range(rnd.randint(6, 14)):
            day = today - timedelta(days=rnd.randint(0, 13))
            start = timezone.make_aware(
                timezone.datetime(day.year, day.month, day.day, rnd.randint(7, 20), rnd.randint(0, 59))
            )
            if start > timezone.now():
                start -= timedelta(days=1)
            mb = rnd.uniform(5, quota / 10)
            bout = int(mb * MB * 0.85)
            bin_ = int(mb * MB * 0.15)
            Session.objects.create(
                instructor=instructor,
                semester_account=account,
                login_time=start,
                logout_time=start + timedelta(minutes=rnd.randint(10, 150)),
                mac_address="02:00:00:%02X:%02X:%02X" % (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)),
                ip_address=f"192.168.88.{rnd.randint(10, 250)}",
                consumption=round((bout + bin_) / MB, 3),
                bytes_in=bin_,
                bytes_out=bout,
                is_active=False,
                logout_reason=rnd.choice([Session.USER, Session.IDLE]),
            )
            total += bout + bin_
        account.total_bytes_used = total
        account.save(update_fields=["total_bytes_used"])
