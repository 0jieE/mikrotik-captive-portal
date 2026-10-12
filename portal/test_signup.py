"""Public self-registration: allowed for anyone on the Wi-Fi, but useless until an administrator approves it."""
from django.urls import reverse

from accounts.models import AuditLog, User
from config.testutils import PASSWORD, BaseTestCase
from instructors.models import Instructor, SemesterAccount
from network.mikrotik import get_router
from network.models import Session

GOOD = {
    "username": "NEW-001",
    "first_name": "Nina",
    "middle_name": "",
    "last_name": "Cruz",
    "extension_name": "",
    "password1": "Sup3r-secret",
    "password2": "Sup3r-secret",
}


class SignupTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.basic_setup()
        self.url = reverse("portal:signup")

    def register(self, **over):
        return self.client.post(self.url, dict(GOOD, **over), REMOTE_ADDR="10.0.0.7")

    # ---- the form ---------------------------------------------------------------------------
    def test_page_renders_and_login_links_to_it(self):
        r = self.client.get(self.url)
        self.assertContains(r, "Username")
        self.assertNotContains(r, "Email")
        self.assertNotContains(r, "Department")
        self.assertNotContains(r, "ID number")
        self.assertContains(self.client.get(reverse("portal:login")), reverse("portal:signup"))

    def test_valid_signup_creates_a_pending_instructor_with_no_internet_account(self):
        r = self.register()
        self.assertRedirects(r, reverse("portal:signup_done"))
        i = Instructor.objects.get(user__username="NEW-001")
        self.assertEqual(i.approval_status, Instructor.PENDING)
        self.assertTrue(i.user.is_instructor and i.user.is_active and not i.user.is_admin)
        self.assertTrue(i.user.check_password("Sup3r-secret"))
        self.assertFalse(i.user.must_change_password)
        self.assertFalse(SemesterAccount.objects.filter(instructor=i).exists())
        self.assertTrue(AuditLog.objects.filter(action="instructor.signup", target="NEW-001").exists())

    def test_signup_does_not_log_the_visitor_in(self):
        self.register()
        self.assertEqual(self.client.get(reverse("portal:dashboard")).status_code, 302)

    def test_done_page(self):
        self.assertContains(self.client.get(reverse("portal:signup_done")), "waiting for approval")

    def test_existing_and_duplicate_usernames_are_refused(self):
        self.assertContains(self.register(username="S1"), "already taken")
        self.assertContains(self.register(username="s1"), "already taken")  # case-insensitive
        self.register()
        self.assertContains(self.register(), "waiting for approval")
        self.assertEqual(Instructor.objects.filter(user__username__iexact="NEW-001").count(), 1)

    def test_username_used_by_an_admin_is_refused(self):
        self.make_admin("boss")
        self.assertContains(self.register(username="boss"), "already taken")
        self.assertFalse(User.objects.filter(username="boss", is_instructor=True).exists())

    def test_username_with_spaces_is_refused(self):
        self.assertContains(self.register(username="has space"), "must not contain spaces")
        self.assertFalse(User.objects.filter(username="has space").exists())

    def test_rejected_username_cannot_register_again(self):
        self.register()
        Instructor.objects.filter(user__username="NEW-001").update(approval_status=Instructor.REJECTED)
        self.assertContains(self.register(), "not approved")

    def test_password_rules(self):
        self.assertContains(self.register(password2="different-one"), "do not match")
        self.assertContains(self.register(password1="123", password2="123"), "too short")
        self.assertFalse(Instructor.objects.filter(user__username="NEW-001").exists())

    def test_required_fields(self):
        r = self.register(first_name="", last_name="")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Instructor.objects.filter(user__username="NEW-001").exists())

    def test_signups_are_rate_limited_per_device_address(self):
        for n in range(5):
            r = self.client.post(self.url, dict(GOOD, username="RL-%d" % n), REMOTE_ADDR="10.9.9.9")
            self.assertEqual(r.status_code, 302, n)
        r = self.client.post(self.url, dict(GOOD, username="RL-5"), REMOTE_ADDR="10.9.9.9")
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "Too many registrations", status_code=429)
        self.assertFalse(Instructor.objects.filter(user__username="RL-5").exists())
        # a different device is not affected
        self.assertEqual(self.client.post(self.url, dict(GOOD, username="RL-6"), REMOTE_ADDR="10.9.9.10").status_code, 302)

    def test_a_signed_in_instructor_is_sent_to_the_dashboard(self):
        self.client.login(username="s1", password=PASSWORD)
        self.assertRedirects(self.client.get(self.url), reverse("portal:dashboard"), fetch_redirect_response=False)


class PendingInstructorExperienceTests(BaseTestCase):
    """What a registered-but-unapproved instructor sees, and that approval really unlocks the Wi-Fi."""

    def setUp(self):
        super().setUp()
        self.basic_setup()
        self.client.post(reverse("portal:signup"), GOOD, REMOTE_ADDR="10.0.0.7")
        self.login = lambda: self.client.post(
            reverse("portal:login"),
            {"username": "NEW-001", "password": "Sup3r-secret", "mac": "AA:BB:CC:DD:EE:01", "ip": "192.168.88.50"},
        )

    def test_pending_instructor_reaches_the_dashboard_but_is_not_connected(self):
        r = self.login()
        self.assertRedirects(r, reverse("portal:dashboard"), fetch_redirect_response=False)
        self.assertFalse(Session.objects.exists())
        with get_router() as router:
            self.assertEqual(router.active_sessions(), [])
            self.assertEqual(router.list_managed_users(), [])  # nothing was even created on the router
        page = self.client.get(reverse("portal:dashboard"))
        self.assertContains(page, "Registration pending approval")
        self.assertContains(page, "waiting for approval")
        self.assertNotContains(page, "Connect this device")

    def test_connect_button_endpoint_refuses_a_pending_instructor(self):
        self.login()
        r = self.client.post(reverse("portal:connect"), follow=True)
        self.assertContains(r, "waiting for approval")
        self.assertFalse(Session.objects.exists())

    def test_pending_instructor_cannot_open_admin_pages(self):
        self.login()
        self.assertEqual(self.client.get(reverse("adminpanel:instructor_list")).status_code, 302)

    def test_rejected_instructor_stays_on_the_login_page(self):
        Instructor.objects.filter(user__username="NEW-001").update(approval_status=Instructor.REJECTED)
        r = self.login()
        self.assertContains(r, "not approved")
        self.assertEqual(self.client.get(reverse("portal:dashboard")).status_code, 302)
        self.assertFalse(Session.objects.exists())

    def test_login_ignores_the_case_of_the_username(self):
        """Phone keyboards capitalise the first letter; the sign-in must still find the account."""
        from instructors import approval

        approval.approve(Instructor.objects.get(user__username="NEW-001"), self.make_admin())
        r = self.client.post(
            reverse("portal:login"),
            {"username": "new-001", "password": "Sup3r-secret", "mac": "AA:BB:CC:DD:EE:01", "ip": "192.168.88.50"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Session.objects.filter(is_active=True, instructor__user__username="NEW-001").exists())

    def test_after_approval_the_same_login_connects(self):
        from instructors import approval

        admin = self.make_admin()
        approval.approve(Instructor.objects.get(user__username="NEW-001"), admin)
        r = self.login()
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Session.objects.filter(is_active=True, instructor__user__username="NEW-001").exists())
        with get_router() as router:
            self.assertEqual([a["user"] for a in router.active_sessions()], ["NEW-001"])
