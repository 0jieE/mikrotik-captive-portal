from django.urls import reverse

from academics.models import Semester
from accounts.models import AuditLog
from config.testutils import PASSWORD, BaseTestCase
from network.mikrotik import get_router
from network.models import Session
from instructors.models import MB, SemesterAccount

MAC = "AA:BB:CC:DD:EE:01"
IP = "192.168.88.50"


class CaptivePortalLoginTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.basic_setup()
        self.url = reverse("portal:login")

    def post(self, username="s1", password=PASSWORD, **extra):
        data = {"username": username, "password": password, "mac": MAC, "ip": IP, "dst": "http://example.com/"}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_login_page_keeps_hotspot_variables(self):
        r = self.client.get(self.url, {"mac": MAC, "ip": IP, "dst": "http://example.com/"})
        self.assertContains(r, MAC)
        self.assertContains(r, IP)
        self.assertContains(r, "http://example.com/")

    def test_success_logs_in_device_creates_session_and_redirects_to_dst(self):
        r = self.post()
        self.assertRedirects(r, "http://example.com/", fetch_redirect_response=False)
        s = Session.objects.get()
        self.assertTrue(s.is_active)
        self.assertEqual((s.mac_address, s.ip_address), (MAC, IP))
        with get_router() as router:
            self.assertEqual([a["user"] for a in router.active_sessions()], ["s1"])
        self.assertTrue(AuditLog.objects.filter(action="login.success", target="s1").exists())
        # also logged into Django: dashboard reachable
        self.assertEqual(self.client.get(reverse("portal:dashboard")).status_code, 200)

    def test_without_dst_goes_to_dashboard(self):
        self.assertRedirects(self.post(dst=""), reverse("portal:dashboard"))

    def test_javascript_dst_is_ignored(self):
        r = self.post(dst="javascript:alert(1)")
        self.assertRedirects(r, reverse("portal:dashboard"), fetch_redirect_response=False)

    def test_wrong_password(self):
        r = self.post(password="nope")
        self.assertContains(r, "Wrong username or password")
        self.assertFalse(Session.objects.exists())
        self.assertTrue(AuditLog.objects.filter(action="login.failed").exists())

    def test_unknown_user(self):
        self.assertContains(self.post(username="ghost"), "Wrong username or password")

    def test_rate_limit_after_five_failures(self):
        for _ in range(5):
            self.post(password="nope")
        r = self.post()  # right password, but locked out
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "Too many failed attempts", status_code=429)
        self.assertFalse(Session.objects.exists())

    def assertDeniedButOnDashboard(self, text):
        """Right credentials + no internet allowed => signed in to the portal, NOT connected, reason shown."""
        r = self.post()
        self.assertRedirects(r, reverse("portal:dashboard"), fetch_redirect_response=False)
        self.assertFalse(Session.objects.exists())
        with get_router() as router:
            self.assertEqual(router.active_sessions(), [])
        page = self.client.get(reverse("portal:dashboard"))
        self.assertContains(page, text)
        self.assertNotContains(page, "Connect this device")  # denied: no connect button
        self.assertTrue(AuditLog.objects.filter(action="login.denied", target="s1").exists())
        return page

    def test_no_active_semester_goes_to_dashboard_with_reason(self):
        Semester.objects.update(is_active=False)
        self.assertDeniedButOnDashboard("no active semester")

    def test_account_disabled_goes_to_dashboard_with_reason(self):
        self.account.is_active = False
        self.account.save()
        self.assertDeniedButOnDashboard("internet account is disabled")

    def test_quota_used_up_goes_to_dashboard_showing_usage(self):
        SemesterAccount.objects.filter(pk=self.account.pk).update(total_bytes_used=self.policy.quota_bytes)
        page = self.assertDeniedButOnDashboard("Internet paused - quota used up")
        self.assertContains(page, "100%")
        self.assertContains(page, "2048")
        self.assertContains(self.client.get(reverse("portal:history")), "session history")  # other portal pages work too

    def test_quota_message_is_shown_right_after_login(self):
        SemesterAccount.objects.filter(pk=self.account.pk).update(total_bytes_used=self.policy.quota_bytes)
        r = self.post()
        follow = self.client.get(r["Location"])
        self.assertContains(follow, "quota is used up")

    def test_denied_instructor_still_cannot_reach_admin_pages(self):
        SemesterAccount.objects.filter(pk=self.account.pk).update(total_bytes_used=self.policy.quota_bytes)
        self.post()
        self.assertEqual(self.client.get(reverse("adminpanel:instructor_list")).status_code, 302)

    def test_dashboard_connect_button_connects_an_allowed_instructor(self):
        # signed in to the portal but not connected yet (e.g. was kicked, or opened the dashboard from another tab)
        self.client.login(username="s1", password=PASSWORD)
        page = self.client.get(reverse("portal:dashboard"))
        self.assertContains(page, "Connect this device")
        r = self.client.post(reverse("portal:connect"), follow=True)
        self.assertContains(r, "Connected. You can now use the internet.")
        self.assertTrue(Session.objects.filter(is_active=True).exists())
        self.assertNotContains(self.client.get(reverse("portal:dashboard")), "Connect this device")

    def test_dashboard_connect_refuses_when_quota_is_used_up(self):
        SemesterAccount.objects.filter(pk=self.account.pk).update(total_bytes_used=self.policy.quota_bytes)
        self.client.login(username="s1", password=PASSWORD)
        r = self.client.post(reverse("portal:connect"), follow=True)
        self.assertContains(r, "quota is used up")
        self.assertFalse(Session.objects.exists())

    def test_connect_requires_post_and_login(self):
        self.assertEqual(self.client.get(reverse("portal:connect")).status_code, 302)  # anonymous -> login
        self.client.login(username="s1", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("portal:connect")).status_code, 405)

    def test_captive_probe_urls_are_not_used_as_destination(self):
        for probe in ("http://captive.apple.com/hotspot-detect.html", "http://connectivitycheck.gstatic.com/generate_204",
                      "http://www.msftconnecttest.com/connecttest.txt"):
            self.client.logout()
            r = self.post(dst=probe)
            self.assertRedirects(r, reverse("portal:dashboard"), fetch_redirect_response=False, msg_prefix=probe)

    def test_real_destination_still_honoured(self):
        self.assertRedirects(self.post(dst="http://www.wikipedia.org/"), "http://www.wikipedia.org/", fetch_redirect_response=False)

    def test_deactivated_account_stays_on_login_page(self):
        """A deactivated user may NOT get into the portal at all."""
        self.instructor.user.is_active = False
        self.instructor.user.save()
        r = self.post()
        self.assertContains(r, "account is disabled")
        self.assertEqual(self.client.get(reverse("portal:dashboard")).status_code, 302)

    def test_admin_accounts_are_refused_at_the_portal(self):
        self.make_admin()
        self.assertContains(self.post(username="boss"), "Administrators sign in")

    def test_router_down_shows_friendly_message(self):
        self.router.set_offline(True)
        r = self.post()
        self.assertContains(r, "not responding")
        self.assertNotContains(r, "Traceback")

    def test_temp_password_user_is_connected_then_asked_to_change(self):
        self.instructor.user.must_change_password = True
        self.instructor.user.save()
        r = self.post()
        self.assertRedirects(r, reverse("accounts:change_password"), fetch_redirect_response=False)
        self.assertTrue(Session.objects.filter(is_active=True).exists())

    def test_login_page_works_without_mac_or_ip_params_in_mock_mode(self):
        r = self.client.post(self.url, {"username": "s1", "password": PASSWORD})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Session.objects.filter(is_active=True).exists())


class InstructorPortalTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.basic_setup()
        self.client.post(reverse("portal:login"), {"username": "s1", "password": PASSWORD, "mac": MAC, "ip": IP})

    def test_dashboard_shows_connection_and_quota(self):
        self.router.add_traffic("s1", bytes_out=512 * MB)
        from network.sync import sync_sessions_once

        sync_sessions_once()
        r = self.client.get(reverse("portal:dashboard"))
        self.assertContains(r, "Connected")
        self.assertContains(r, IP)
        self.assertContains(r, MAC)
        self.assertContains(r, "512")
        self.assertContains(r, "25%")  # 512 of 2048
        self.assertContains(r, "Standard")
        self.assertEqual(self.client.get(reverse("portal:dashboard_live")).status_code, 200)

    def test_history_shows_only_own_sessions(self):
        other, other_acct = self.make_instructor("s2")
        from django.utils import timezone

        Session.objects.create(instructor=other, semester_account=other_acct, login_time=timezone.now(), is_active=False,
                               mac_address="DE:AD:BE:EF:00:01", ip_address="10.9.9.9", consumption=77.7)
        Session.objects.filter(instructor=self.instructor).update(is_active=False, logout_time=timezone.now())
        r = self.client.get(reverse("portal:history"))
        self.assertContains(r, "Disconnected")
        self.assertNotContains(r, "77.7")

    def test_instructor_never_sees_other_instructors_data_on_dashboard(self):
        other, _ = self.make_instructor("s2")
        r = self.client.get(reverse("portal:dashboard"))
        self.assertNotContains(r, other.user.username)

    def test_disconnect_keeps_login_but_drops_wifi(self):
        self.client.post(reverse("portal:disconnect"))
        self.assertFalse(Session.objects.filter(is_active=True).exists())
        with get_router() as router:
            self.assertEqual(router.active_sessions(), [])
        self.assertEqual(self.client.get(reverse("portal:dashboard")).status_code, 200)

    def test_logout_disconnects_and_signs_out(self):
        self.client.post(reverse("portal:logout"))
        sess = Session.objects.get()
        self.assertFalse(sess.is_active)
        self.assertEqual(sess.logout_reason, Session.USER)
        with get_router() as router:
            self.assertEqual(router.active_sessions(), [])
        self.assertEqual(self.client.get(reverse("portal:dashboard")).status_code, 302)

    def test_change_password(self):
        r = self.client.post(reverse("accounts:change_password"),
                             {"old_password": PASSWORD, "new_password1": "Another-pass-77", "new_password2": "Another-pass-77"})
        self.assertEqual(r.status_code, 302)
        self.instructor.user.refresh_from_db()
        self.assertTrue(self.instructor.user.check_password("Another-pass-77"))


class KickedInstructorTests(BaseTestCase):
    """A kick only disconnects; the instructor can open the dashboard and reconnect immediately."""

    def setUp(self):
        super().setUp()
        self.basic_setup()
        self.client.post(reverse("portal:login"), {"username": "s1", "password": PASSWORD, "mac": MAC, "ip": IP})

    def kick(self):
        from network.services import kick_sessions

        kick_sessions(Session.objects.filter(is_active=True).select_related("instructor__user"), Session.ADMIN_KICK)

    def test_kicked_instructor_can_still_open_the_portal_and_is_told_why(self):
        self.kick()
        r = self.client.get(reverse("portal:dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "disconnected by an administrator")
        self.assertContains(r, "Connect this device")

    def test_kicked_instructor_reconnects_with_the_button_or_by_signing_in_again(self):
        self.kick()
        self.client.post(reverse("portal:connect"))
        self.assertTrue(Session.objects.filter(is_active=True).exists())
        self.kick()
        fresh = self.client_class()
        r = fresh.post(reverse("portal:login"), {"username": "s1", "password": PASSWORD, "mac": MAC, "ip": IP})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Session.objects.filter(is_active=True).exists())

    def test_dashboard_renders_when_the_instructor_has_never_connected(self):
        Session.objects.all().delete()
        self.assertEqual(self.client.get(reverse("portal:dashboard")).status_code, 200)


class UsernameLabelTests(BaseTestCase):
    def test_login_page_asks_for_a_username(self):
        r = self.client.get(reverse("portal:login"))
        self.assertContains(r, "Username")
        self.assertContains(r, "Use your username and password.")
        self.assertNotContains(r, "ID number")
