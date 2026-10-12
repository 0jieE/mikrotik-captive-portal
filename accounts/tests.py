from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, LoginAttempt
from accounts.ratelimit import lockout_remaining, record_attempt
from config.testutils import PASSWORD, BaseTestCase


class RateLimitTests(BaseTestCase):
    def test_five_failures_lock_for_five_minutes(self):
        for _ in range(4):
            record_attempt("bob", "1.1.1.1", False)
        self.assertEqual(lockout_remaining("bob", "1.1.1.1"), 0)
        record_attempt("bob", "1.1.1.1", False)
        wait = lockout_remaining("bob", "1.1.1.1")
        self.assertTrue(0 < wait <= 300)

    def test_lock_is_per_username_and_ip(self):
        for _ in range(5):
            record_attempt("bob", "1.1.1.1", False)
        self.assertEqual(lockout_remaining("bob", "2.2.2.2"), 0)
        self.assertEqual(lockout_remaining("alice", "1.1.1.1"), 0)

    def test_old_failures_expire(self):
        for _ in range(5):
            record_attempt("bob", "1.1.1.1", False)
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(lockout_remaining("bob", "1.1.1.1"), 0)

    def test_success_resets_the_counter(self):
        for _ in range(4):
            record_attempt("bob", "1.1.1.1", False)
        record_attempt("bob", "1.1.1.1", True)
        record_attempt("bob", "1.1.1.1", False)
        self.assertEqual(lockout_remaining("bob", "1.1.1.1"), 0)


class StaffLoginTests(BaseTestCase):
    def test_admin_can_sign_in(self):
        self.make_admin()
        r = self.client.post(reverse("accounts:staff_login"), {"username": "boss", "password": PASSWORD})
        self.assertRedirects(r, reverse("adminpanel:dashboard"))
        self.assertTrue(AuditLog.objects.filter(action="login.success", target="boss").exists())

    def test_instructors_cannot_use_the_staff_login(self):
        self.basic_setup()
        r = self.client.post(reverse("accounts:staff_login"), {"username": "s1", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Invalid administrator")

    def test_failures_are_logged_and_lock_out(self):
        self.make_admin()
        url = reverse("accounts:staff_login")
        for _ in range(5):
            self.client.post(url, {"username": "boss", "password": "wrong"})
        r = self.client.post(url, {"username": "boss", "password": PASSWORD})  # correct but locked
        self.assertContains(r, "Too many failed attempts")
        self.assertEqual(AuditLog.objects.filter(action="login.failed").count(), 5)
        self.assertTrue(AuditLog.objects.filter(action="login.locked").exists())

    def test_inactive_admin_cannot_sign_in(self):
        self.make_admin(is_active=False)
        r = self.client.post(reverse("accounts:staff_login"), {"username": "boss", "password": PASSWORD})
        self.assertContains(r, "Invalid administrator")

    def test_logout_requires_post(self):
        self.make_admin()
        self.client.login(username="boss", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("adminpanel:dashboard")).status_code, 302)


class RbacTests(BaseTestCase):
    ADMIN_URLS = [
        "adminpanel:dashboard", "adminpanel:instructor_list", "adminpanel:instructor_add", "adminpanel:masterlist_upload",
        "adminpanel:admin_list", "adminpanel:semester_list", "adminpanel:policy_list", "adminpanel:monitoring",
        "adminpanel:monitoring_live", "adminpanel:reports", "adminpanel:audit_logs", "adminpanel:report_csv",
    ]

    def test_anonymous_is_sent_to_login(self):
        for name in self.ADMIN_URLS:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/accounts/login/", r["Location"], name)
        self.assertIn("/portal/login/", self.client.get(reverse("portal:dashboard"))["Location"])

    def test_instructors_cannot_reach_any_admin_page(self):
        self.basic_setup()
        self.client.login(username="s1", password=PASSWORD)
        for name in self.ADMIN_URLS:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/portal/", r["Location"], name)
        # and cannot POST admin actions either
        r = self.client.post(reverse("adminpanel:instructor_bulk"), {"action": "disable", "ids": [self.instructor.pk]})
        self.assertEqual(r.status_code, 302)
        self.instructor.user.refresh_from_db()
        self.assertTrue(self.instructor.user.is_active)

    def test_admins_cannot_use_the_instructor_portal_pages(self):
        self.make_admin()
        self.client.login(username="boss", password=PASSWORD)
        r = self.client.get(reverse("portal:dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin-panel/", r["Location"])

    def test_django_admin_is_superusers_only(self):
        self.make_admin(is_staff=True)  # staff but not superuser
        self.client.login(username="boss", password=PASSWORD)
        self.assertEqual(self.client.get("/django-admin/").status_code, 302)
        from accounts.models import User

        User.objects.create_superuser("root", password=PASSWORD)
        self.client.login(username="root", password=PASSWORD)
        self.assertEqual(self.client.get("/django-admin/").status_code, 200)

    def test_forced_password_change(self):
        self.basic_setup()
        self.instructor.user.must_change_password = True
        self.instructor.user.save()
        self.client.login(username="s1", password=PASSWORD)
        r = self.client.get(reverse("portal:history"))
        self.assertRedirects(r, reverse("accounts:change_password"), fetch_redirect_response=False)
        r = self.client.post(reverse("accounts:change_password"),
                             {"old_password": PASSWORD, "new_password1": "BrandNew-pass-9", "new_password2": "BrandNew-pass-9"})
        self.assertEqual(r.status_code, 302)
        self.instructor.user.refresh_from_db()
        self.assertFalse(self.instructor.user.must_change_password)
        self.assertEqual(self.client.get(reverse("portal:history")).status_code, 200)


class CreateAdminCommandTests(BaseTestCase):
    def run_cmd(self, *args):
        from django.core.management import call_command

        call_command("create_admin", *args, stdout=__import__("io").StringIO())

    def test_creates_a_working_panel_admin(self):
        self.run_cmd("--username", "client", "--password", "Str0ng-pass!")
        from accounts.models import User

        u = User.objects.get(username="client")
        self.assertTrue(u.is_admin and u.is_active and u.is_superuser and not u.is_instructor)
        self.assertTrue(self.client.login(username="client", password="Str0ng-pass!"))
        self.assertEqual(self.client.get(reverse("adminpanel:dashboard")).status_code, 200)

    def test_rerun_resets_password(self):
        self.run_cmd("--username", "client", "--password", "First-pass-1")
        self.run_cmd("--username", "client", "--password", "Second-pass-2")
        from accounts.models import User

        self.assertTrue(User.objects.get(username="client").check_password("Second-pass-2"))

    def test_refuses_instructor_usernames_and_short_passwords(self):
        from django.core.management.base import CommandError

        self.basic_setup()
        with self.assertRaises(CommandError):
            self.run_cmd("--username", "s1", "--password", "Str0ng-pass!")
        with self.assertRaises(CommandError):
            self.run_cmd("--username", "x", "--password", "short")
