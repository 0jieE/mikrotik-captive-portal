from datetime import date

from academics.models import BandwidthPolicy, SchoolYear, Semester
from config.testutils import BaseTestCase


class SemesterRuleTests(BaseTestCase):
    def test_only_one_semester_can_be_active(self):
        first = self.make_semester(active=True)
        second = self.make_semester(active=True, semester=Semester.SECOND)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(Semester.objects.filter(is_active=True).count(), 1)
        self.assertEqual(Semester.get_active(), second)

    def test_reactivating_an_old_semester_switches_back(self):
        first = self.make_semester(active=True)
        second = self.make_semester(active=True, semester=Semester.SECOND)
        first.is_active = True
        first.save()
        second.refresh_from_db()
        self.assertFalse(second.is_active)
        self.assertEqual(Semester.get_active(), first)

    def test_saving_an_inactive_semester_keeps_the_active_one(self):
        active = self.make_semester(active=True)
        self.make_semester(active=False, semester=Semester.SUMMER)
        active.refresh_from_db()
        self.assertTrue(active.is_active)

    def test_school_year_format_validation(self):
        from django.core.exceptions import ValidationError

        for bad in ("2026", "2026-2028", "abcd-efgh"):
            with self.assertRaises(ValidationError):
                SchoolYear(sy=bad).full_clean()
        SchoolYear(sy="2026-2027").full_clean()

    def test_end_date_cannot_precede_start(self):
        from django.core.exceptions import ValidationError

        sy = SchoolYear.objects.create(sy="2026-2027")
        with self.assertRaises(ValidationError):
            Semester(semester="1st", sy=sy, start_date=date(2026, 8, 1), end_date=date(2026, 7, 1)).full_clean()


class PolicyTests(BaseTestCase):
    def test_profile_name_is_generated_and_unique(self):
        a = self.make_policy("Faculty assisted", default=False)
        b = self.make_policy("Faculty-assisted!", default=False)
        self.assertEqual(a.mikrotik_profile_name, "cdk-faculty-assisted")
        self.assertNotEqual(a.mikrotik_profile_name, b.mikrotik_profile_name)

    def test_only_one_default(self):
        a = self.make_policy("A", default=True)
        b = self.make_policy("B", default=True)
        a.refresh_from_db()
        self.assertFalse(a.is_default)
        self.assertEqual(BandwidthPolicy.get_default(), b)

    def test_rate_limit_is_upload_slash_download(self):
        p = self.make_policy("Asym", down=4, up=1, default=False)
        self.assertEqual(p.rate_limit, "1M/4M")
        q = self.make_policy("Fractional", down=0.5, up=0.25, default=False)
        self.assertEqual(q.rate_limit, "250k/500k")

    def test_quota_bytes_and_unlimited(self):
        self.assertEqual(self.make_policy("Q", quota=1, default=False).quota_bytes, 1024 * 1024)
        self.assertIsNone(self.make_policy("U", quota=None, default=False).quota_bytes)

    def test_session_timeout_string(self):
        self.assertEqual(self.make_policy("T", session_timeout_minutes=90, default=False).session_timeout, "1h30m")
        self.assertEqual(self.make_policy("T2", default=False).session_timeout, "")
