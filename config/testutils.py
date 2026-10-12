"""Shared helpers for the test-suite. Every test runs against the in-memory mock router."""
from datetime import date

from django.test import TestCase, override_settings

from academics.models import BandwidthPolicy, SchoolYear, Semester
from accounts.models import User
from network.mikrotik import MockMikroTik
from instructors.models import SemesterAccount, Instructor

PASSWORD = "pw-12345"


@override_settings(
    MIKROTIK_MOCK=True,
    MIKROTIK_MOCK_STATE_FILE=None,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher", "accounts.hashers.BulkPBKDF2PasswordHasher"],
)
class BaseTestCase(TestCase):
    """Resets the mock router before each test and provides small factories."""

    def setUp(self):
        super().setUp()
        MockMikroTik.reset()
        self.router = MockMikroTik()

    # factories ------------------------------------------------------------------------
    def make_policy(self, name="Standard", down=2, up=2, quota=2048, default=True, **kw):
        return BandwidthPolicy.objects.create(
            name=name, max_speed_down_mbps=down, max_speed_up_mbps=up, quota_limit_mb=quota, is_default=default, **kw
        )

    def make_semester(self, active=True, semester=Semester.FIRST, sy="2026-2027"):
        year, _ = SchoolYear.objects.get_or_create(sy=sy)
        return Semester.objects.create(
            semester=semester, sy=year, start_date=date(2026, 6, 1), end_date=date(2026, 10, 30), is_active=active
        )

    def make_instructor(self, username="s1", active=True, policy=None, semester=None,
                        account=True, account_active=True, password=PASSWORD):
        user = User.objects.create_user(
            username=username, password=password, first_name=username.title(), last_name="Tester",
            is_instructor=True, is_active=active,
        )
        instructor = Instructor.objects.create(user=user)
        acct = None
        if account:
            policy = policy or BandwidthPolicy.get_default()
            semester = semester or Semester.get_active()
            acct = SemesterAccount.objects.create(instructor=instructor, semester=semester, policy=policy, is_active=account_active)
        return instructor, acct

    def make_admin(self, username="boss", password=PASSWORD, **kw):
        return User.objects.create_user(username=username, password=password, is_admin=True, **kw)

    def basic_setup(self):
        """Active semester + default policy + one instructor with an account."""
        self.policy = self.make_policy()
        self.semester = self.make_semester()
        self.instructor, self.account = self.make_instructor()
        return self.instructor, self.account
