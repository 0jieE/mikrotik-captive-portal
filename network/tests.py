from django.utils import timezone

from academics.models import Semester
from config.testutils import BaseTestCase
from network.mikrotik import MANAGED, RouterError, get_router, parse_uptime
from network.models import Session
from network.services import (
    connect_instructor,
    disconnect_user,
    evaluate_access,
    set_active_semester,
    sync_router_full,
)
from network.sync import sync_sessions_once
from instructors.models import MB, SemesterAccount

MAC = "AA:BB:CC:DD:EE:01"
IP = "192.168.88.50"


class AccessRuleTests(BaseTestCase):
    """Internet access only if: user active + semester active + account active + quota not exceeded."""

    def setUp(self):
        super().setUp()
        self.basic_setup()

    def test_allowed_when_all_rules_pass(self):
        r = evaluate_access(self.instructor.user)
        self.assertTrue(r.allowed)
        self.assertEqual(r.account, self.account)

    def test_inactive_user(self):
        self.instructor.user.is_active = False
        self.instructor.user.save()
        r = evaluate_access(self.instructor.user)
        self.assertEqual((r.allowed, r.code), (False, "inactive_user"))

    def test_no_active_semester(self):
        Semester.objects.update(is_active=False)
        self.assertEqual(evaluate_access(self.instructor.user).code, "no_semester")

    def test_no_account_for_active_semester(self):
        self.account.delete()
        self.assertEqual(evaluate_access(self.instructor.user).code, "no_account")

    def test_account_from_previous_semester_does_not_count(self):
        new = self.make_semester(active=True, semester=Semester.SECOND)
        self.assertEqual(evaluate_access(self.instructor.user).code, "no_account")
        SemesterAccount.objects.create(instructor=self.instructor, semester=new, policy=self.policy)
        self.assertTrue(evaluate_access(self.instructor.user).allowed)

    def test_account_disabled(self):
        self.account.is_active = False
        self.account.save()
        self.assertEqual(evaluate_access(self.instructor.user).code, "account_disabled")

    def test_quota_exceeded(self):
        self.account.total_bytes_used = self.policy.quota_bytes
        self.account.save()
        r = evaluate_access(self.instructor.user)
        self.assertEqual((r.allowed, r.code), (False, "quota"))

    def test_unlimited_policy_never_hits_quota(self):
        self.policy.quota_limit_mb = None
        self.policy.save()
        self.account.total_bytes_used = 10**12
        self.account.save()
        self.assertTrue(evaluate_access(self.instructor.user).allowed)

    def test_messages_are_human_readable(self):
        self.account.is_active = False
        self.account.save()
        self.assertIn("internet account is disabled", evaluate_access(self.instructor.user).message.lower())

    def test_non_instructor_user(self):
        self.assertEqual(evaluate_access(self.make_admin()).code, "not_instructor")


class ConnectFlowTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.basic_setup()

    def test_successful_connect_mirrors_to_router_and_creates_session(self):
        res = connect_instructor(self.instructor.user, MAC, IP)
        self.assertTrue(res.success, res.message)
        s = res.session
        self.assertTrue(s.is_active)
        self.assertEqual((s.mac_address, s.ip_address), (MAC, IP))
        with get_router() as r:
            users = {u["name"] for u in r.list_managed_users()}
            actives = r.active_sessions()
            profile = r.state["profiles"]["cdk-standard"]
            user = r.state["users"]["s1"]
        self.assertIn("s1", users)
        self.assertEqual(actives[0]["user"], "s1")
        self.assertEqual(s.mikrotik_session_id, actives[0]["id"])
        self.assertEqual(profile["rate-limit"], "2M/2M")
        self.assertEqual(profile["shared-users"], 1)
        self.assertEqual(user["comment"], MANAGED)
        self.assertEqual(user["password"], self.account.hotspot_password)
        self.assertEqual(user["limit_bytes_total"], self.policy.quota_bytes)  # nothing used yet

    def test_limit_bytes_is_remaining_quota(self):
        self.account.total_bytes_used = 500 * MB
        self.account.save()
        connect_instructor(self.instructor.user, MAC, IP)
        with get_router() as r:
            self.assertEqual(r.state["users"]["s1"]["limit_bytes_total"], self.policy.quota_bytes - 500 * MB)

    def test_denied_instructors_never_reach_the_router(self):
        self.account.is_active = False
        self.account.save()
        res = connect_instructor(self.instructor.user, MAC, IP)
        self.assertFalse(res.success)
        self.assertEqual(res.code, "account_disabled")
        with get_router() as r:
            self.assertEqual(r.active_sessions(), [])
            self.assertEqual(r.list_managed_users(), [])
        self.assertFalse(Session.objects.exists())

    def test_hotspot_password_is_random_and_not_the_login_password(self):
        _, other = self.make_instructor("s2")
        self.assertNotEqual(self.account.hotspot_password, other.hotspot_password)
        self.assertGreaterEqual(len(self.account.hotspot_password), 16)

    def test_second_device_replaces_first(self):
        first = connect_instructor(self.instructor.user, MAC, IP).session
        second = connect_instructor(self.instructor.user, "AA:BB:CC:DD:EE:02", "192.168.88.51")
        self.assertTrue(second.success, second.message)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertEqual(first.logout_reason, Session.USER)
        with get_router() as r:
            self.assertEqual(len(r.active_sessions()), 1)
            self.assertEqual(r.active_sessions()[0]["mac"], "AA:BB:CC:DD:EE:02")

    def test_router_offline_gives_friendly_failure(self):
        self.router.set_offline(True)
        res = connect_instructor(self.instructor.user, MAC, IP)
        self.assertFalse(res.success)
        self.assertEqual(res.code, "router_error")
        self.assertNotIn("Traceback", res.message)
        self.assertFalse(Session.objects.exists())

    def test_missing_ip(self):
        res = connect_instructor(self.instructor.user, MAC, "")
        self.assertEqual(res.code, "no_ip")

    def test_disconnect_closes_session_and_removes_active_entry(self):
        sess = connect_instructor(self.instructor.user, MAC, IP).session
        self.router.add_traffic("s1", bytes_in=1 * MB, bytes_out=9 * MB)
        self.assertEqual(disconnect_user(self.instructor.user), 1)
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)
        self.assertEqual(sess.logout_reason, Session.USER)
        self.assertIsNotNone(sess.logout_time)
        self.assertAlmostEqual(sess.consumption, 10.0, places=2)
        self.account.refresh_from_db()
        self.assertEqual(self.account.total_bytes_used, 10 * MB)
        with get_router() as r:
            self.assertEqual(r.active_sessions(), [])


class QuotaEnforcementTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.policy = self.make_policy(quota=100)  # 100 MB
        self.semester = self.make_semester()
        self.instructor, self.account = self.make_instructor()

    def test_usage_is_added_to_the_semester_account(self):
        connect_instructor(self.instructor.user, MAC, IP)
        self.router.add_traffic("s1", bytes_in=5 * MB, bytes_out=20 * MB)
        sync_sessions_once()
        sync_sessions_once()  # idempotent: second pass adds nothing
        self.account.refresh_from_db()
        self.assertEqual(self.account.total_bytes_used, 25 * MB)
        s = Session.objects.get()
        self.assertAlmostEqual(s.consumption, 25.0, places=2)
        self.assertEqual((s.bytes_in, s.bytes_out), (5 * MB, 20 * MB))

    def test_router_enforced_quota_is_recorded_with_final_bytes(self):
        """RouterOS drops the user at limit-bytes-total; sync must still record the usage and why."""
        sess = connect_instructor(self.instructor.user, MAC, IP).session
        self.router.add_traffic("s1", bytes_in=0, bytes_out=120 * MB)  # mock router drops the session itself
        stats = sync_sessions_once()
        self.assertEqual(stats["closed"], 1)
        sess.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(sess.logout_reason, Session.QUOTA)
        self.assertGreaterEqual(self.account.total_bytes_used, 100 * MB)
        self.assertTrue(self.account.quota_exceeded)

    def test_quota_enforced_by_django_when_router_limit_is_not_reached(self):
        """Usage recorded elsewhere pushes the account over quota while the router still shows a live session."""
        sess = connect_instructor(self.instructor.user, MAC, IP).session
        self.router.add_traffic("s1", bytes_in=0, bytes_out=10 * MB)
        # Another source of usage (e.g. an earlier sync) already consumed the rest of the quota.
        SemesterAccount.objects.filter(pk=self.account.pk).update(total_bytes_used=95 * MB)
        sync_sessions_once()
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)
        self.assertEqual(sess.logout_reason, Session.QUOTA)
        with get_router() as r:
            self.assertEqual(r.active_sessions(), [])  # kicked
            self.assertTrue(r.state["users"]["s1"]["disabled"])

    def test_instructor_over_quota_cannot_log_in_again(self):
        connect_instructor(self.instructor.user, MAC, IP)
        SemesterAccount.objects.filter(pk=self.account.pk).update(total_bytes_used=100 * MB)
        sync_sessions_once()
        res = connect_instructor(self.instructor.user, MAC, IP)
        self.assertFalse(res.success)
        self.assertEqual(res.code, "quota")
        self.assertIn("quota", res.message.lower())

    def test_quota_is_cumulative_across_sessions(self):
        connect_instructor(self.instructor.user, MAC, IP)
        self.router.add_traffic("s1", bytes_out=30 * MB)
        disconnect_user(self.instructor.user)
        connect_instructor(self.instructor.user, MAC, IP)
        with get_router() as r:
            # router limit is only what is LEFT, and counters were reset on login
            self.assertEqual(r.state["users"]["s1"]["limit_bytes_total"], 70 * MB)
            self.assertEqual(r.state["users"]["s1"]["bytes_out"], 0)
        self.router.add_traffic("s1", bytes_out=30 * MB)
        disconnect_user(self.instructor.user)
        self.account.refresh_from_db()
        self.assertEqual(self.account.total_bytes_used, 60 * MB)
        self.assertEqual(self.account.remaining_bytes, 40 * MB)
        self.assertEqual(self.account.quota_percent, 60)


class SyncSessionsTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.basic_setup()

    def test_vanished_session_is_closed_as_idle(self):
        sess = connect_instructor(self.instructor.user, MAC, IP).session
        self.router.vanish("s1")
        sync_sessions_once()
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)
        self.assertEqual(sess.logout_reason, Session.IDLE)

    def test_unknown_active_entry_for_valid_instructor_is_adopted(self):
        connect_instructor(self.instructor.user, MAC, IP)
        self.router.vanish("s1")
        sync_sessions_once()
        self.router.inject_active("s1", "AA:BB:CC:DD:EE:09", "192.168.88.60")  # e.g. MAC-cookie auto login
        stats = sync_sessions_once()
        self.assertEqual(stats["adopted"], 1)
        self.assertEqual(Session.objects.filter(is_active=True).count(), 1)

    def test_unknown_active_entry_for_denied_instructor_is_kicked(self):
        self.account.is_active = False
        self.account.save()
        with get_router() as r:
            r.ensure_profile("cdk-standard", "2M/2M")
            r.upsert_user("s1", "x", "cdk-standard")
        self.router.inject_active("s1", MAC, IP)
        sync_sessions_once()
        with get_router() as r:
            self.assertEqual(r.active_sessions(), [])

    def test_unmanaged_router_users_are_left_alone(self):
        self.router.connect()
        self.router.state["users"]["wizard"] = {"name": "wizard", "password": "x", "profile": "default", "comment": "", "disabled": False,
                                                 "bytes_in": 0, "bytes_out": 0, "limit_bytes_total": 0}
        self.router._save()
        self.router.inject_active("wizard", MAC, IP)
        sync_sessions_once()
        with get_router() as r:
            self.assertEqual(len(r.active_sessions()), 1)

    def test_deactivated_instructor_is_kicked_by_sync(self):
        sess = connect_instructor(self.instructor.user, MAC, IP).session
        self.instructor.user.is_active = False
        self.instructor.user.save()
        stats = sync_sessions_once()
        sess.refresh_from_db()
        self.assertEqual(stats["kicked"], 1)
        self.assertFalse(sess.is_active)
        self.assertEqual(sess.logout_reason, Session.ADMIN_KICK)

    def test_sync_router_full_disables_users_django_says_are_off(self):
        connect_instructor(self.instructor.user, MAC, IP)
        self.account.is_active = False
        self.account.save()
        summary = sync_router_full()
        self.assertGreaterEqual(summary["users_disabled"], 1)
        with get_router() as r:
            self.assertTrue(r.state["users"]["s1"]["disabled"])
            self.assertEqual(r.active_sessions(), [])  # and the live session is dropped

    def test_sync_router_full_pushes_active_accounts(self):
        summary = sync_router_full()
        self.assertEqual(summary["policies"], 1)
        self.assertEqual(summary["users_pushed"], 1)
        with get_router() as r:
            self.assertFalse(r.state["users"]["s1"]["disabled"])


class SemesterSwitchTests(BaseTestCase):
    def test_setting_active_semester_winds_down_the_previous_one(self):
        self.basic_setup()
        sess = connect_instructor(self.instructor.user, MAC, IP).session
        new = self.make_semester(active=False, semester=Semester.SECOND)
        warnings = set_active_semester(new)
        self.assertEqual(warnings, [])
        self.assertEqual(Semester.get_active(), new)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)
        self.assertEqual(sess.logout_reason, Session.SEMESTER_ENDED)
        with get_router() as r:
            self.assertTrue(r.state["users"]["s1"]["disabled"])
            self.assertEqual(r.active_sessions(), [])
        # instructor has no account in the new semester -> denied
        self.assertEqual(evaluate_access(self.instructor.user).code, "no_account")

    def test_router_down_still_switches_semester_and_warns(self):
        self.basic_setup()
        connect_instructor(self.instructor.user, MAC, IP)
        new = self.make_semester(active=False, semester=Semester.SECOND)
        self.router.set_offline(True)
        warnings = set_active_semester(new)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(Semester.get_active(), new)
        self.router.set_offline(False)
        sync_sessions_once()  # reconciles: old-semester session is kicked
        self.assertFalse(Session.objects.filter(is_active=True).exists())


class RouterSafetyTests(BaseTestCase):
    def test_refuses_to_touch_unmanaged_users_and_profiles(self):
        self.router.connect()
        self.router.state["users"]["bob"] = {"name": "bob", "comment": "someone else's", "disabled": False, "bytes_in": 0,
                                              "bytes_out": 0, "limit_bytes_total": 0, "password": "p", "profile": "default"}
        self.router.state["profiles"]["default"] = {"name": "default"}
        self.router._save()
        with get_router() as r:
            with self.assertRaises(RouterError):
                r.upsert_user("bob", "x", "default")
            with self.assertRaises(RouterError):
                r.disable_user("bob")
            with self.assertRaises(RouterError):
                r.ensure_profile("default", "1M/1M")

    def test_test_user_from_the_wizard_is_taken_over(self):
        self.router.connect()
        self.router.state["profiles"]["cdk-standard"] = {"name": "cdk-standard"}
        self.router.state["users"]["test"] = {"name": "test", "comment": "", "disabled": False, "bytes_in": 0, "bytes_out": 0,
                                               "limit_bytes_total": 0, "password": "old", "profile": "cdk-standard"}
        self.router._save()
        with get_router() as r:
            r.upsert_user("test", "new", "cdk-standard")
            self.assertEqual(r.state["users"]["test"]["comment"], MANAGED)
            self.assertEqual(r.state["users"]["test"]["password"], "new")

    def test_uptime_parser(self):
        self.assertEqual(parse_uptime("1d2h3m4s"), 86400 + 7200 + 180 + 4)
        self.assertEqual(parse_uptime("1w"), 604800)
        self.assertEqual(parse_uptime(""), 0)


class SessionModelTests(BaseTestCase):
    def test_duration_for_open_session(self):
        self.basic_setup()
        s = Session.objects.create(instructor=self.instructor, semester_account=self.account, login_time=timezone.now())
        self.assertGreaterEqual(s.duration.total_seconds(), 0)


class ResolveDeviceTests(BaseTestCase):
    """The ?mac=&ip= values come from the browser and must not let an instructor log in someone else's device."""

    class FakeReal:
        is_mock = False

        def __init__(self, hosts):
            self.hosts = hosts

        def host_lookup(self, ip):
            return self.hosts.get(ip, "")

    def test_real_router_forces_ip_to_the_requesters_address(self):
        from network.services import resolve_device

        r = self.FakeReal({"192.168.88.20": "AA:AA:AA:AA:AA:20", "192.168.88.99": "BB:BB:BB:BB:BB:99"})
        mac, ip = resolve_device(r, "BB:BB:BB:BB:BB:99", "192.168.88.99", peer_ip="192.168.88.20")  # spoofed victim
        self.assertEqual((mac, ip), ("AA:AA:AA:AA:AA:20", "192.168.88.20"))

    def test_router_host_table_wins_over_posted_mac(self):
        from network.services import resolve_device

        r = self.FakeReal({"192.168.88.20": "AA:AA:AA:AA:AA:20"})
        self.assertEqual(resolve_device(r, "DE:AD:BE:EF:00:01", "192.168.88.20", "192.168.88.20")[0], "AA:AA:AA:AA:AA:20")

    def test_posted_mac_is_used_only_when_router_has_no_entry(self):
        from network.services import resolve_device

        r = self.FakeReal({})
        self.assertEqual(resolve_device(r, "aa-bb-cc-dd-ee-01", "192.168.88.20", "192.168.88.20"), ("AA:BB:CC:DD:EE:01", "192.168.88.20"))

    def test_unknown_device_is_rejected_on_a_real_router(self):
        from network.services import resolve_device

        mac, _ = resolve_device(self.FakeReal({}), "", "192.168.88.20", "192.168.88.20")
        self.assertEqual(mac, "")

    def test_mock_mode_trusts_params_and_fakes_a_mac_for_local_dev(self):
        self.basic_setup()
        res = connect_instructor(self.instructor.user, "", "127.0.0.1", peer_ip="127.0.0.1")
        self.assertTrue(res.success)
        self.assertTrue(res.session.mac_address.startswith("02:00:"))


class SyncHeartbeatAndIdReuseTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.basic_setup()

    def test_health_reports_never_then_running_then_stale(self):
        from datetime import timedelta

        from network.models import SyncState
        from network.sync import run_and_record, sync_health

        self.assertEqual(sync_health()["running"], False)
        self.assertTrue(sync_health()["never"])
        run_and_record()
        self.assertTrue(sync_health()["running"])
        SyncState.objects.update(last_run=timezone.now() - timedelta(minutes=10))
        h = sync_health()
        self.assertFalse(h["running"])
        self.assertGreater(h["age"], 500)

    def test_router_failure_is_recorded_and_re_raised(self):
        from network.sync import run_and_record, sync_health

        self.router.set_offline(True)
        with self.assertRaises(RouterError):
            run_and_record()
        self.assertIn("mock router set offline", sync_health()["error"])

    def test_admin_dashboard_warns_when_sync_is_not_running(self):
        from django.urls import reverse

        self.make_admin()
        self.client.login(username="boss", password="pw-12345")
        r = self.client.get(reverse("adminpanel:monitoring_live"))
        self.assertContains(r, "Usage sync is not running")
        from network.sync import run_and_record

        run_and_record()
        self.assertNotContains(self.client.get(reverse("adminpanel:monitoring_live")), "Usage sync is not running")

    def test_reused_router_session_id_is_not_credited_to_another_instructor(self):
        """RouterOS reuses session ids. A stale session of instructor A must not pick up instructor B's traffic."""
        other, _ = self.make_instructor("s2")
        stale = connect_instructor(self.instructor.user, MAC, IP).session  # s1 online
        self.router.vanish("s1")  # router drops s1, Django does not know yet
        res = connect_instructor(other.user, MAC, IP)  # s2 logs in from the same device
        self.assertTrue(res.success)
        # force the id collision seen on the real router
        Session.objects.filter(pk=stale.pk).update(mikrotik_session_id=res.session.mikrotik_session_id)
        self.router.add_traffic("s2", bytes_out=7 * MB)
        sync_sessions_once()
        stale.refresh_from_db()
        res.session.refresh_from_db()
        self.assertFalse(stale.is_active)  # s1's session closed as gone
        self.assertEqual(stale.bytes_out, 0)  # ...and got none of s2's bytes
        self.assertEqual(res.session.bytes_out, 7 * MB)
        self.account.refresh_from_db()
        self.assertEqual(self.account.total_bytes_used, 0)
