import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from academics.models import BandwidthPolicy, Semester
from accounts.models import AuditLog, User
from config.testutils import PASSWORD, BaseTestCase
from network.mikrotik import get_router
from network.models import Session
from network.services import connect_instructor
from instructors.models import MB, SemesterAccount, Instructor

HEADER = "username,first_name,middle_name,last_name,extension_name\n"


class AdminTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.basic_setup()
        self.admin = self.make_admin()
        self.client.login(username="boss", password=PASSWORD)

    def connect(self, instructor=None):
        instructor = instructor or self.instructor
        return connect_instructor(instructor.user, "AA:BB:CC:DD:EE:01", "192.168.88.50").session


class PagesRenderTests(AdminTestCase):
    def test_every_admin_page_renders(self):
        self.connect()
        self.router.add_traffic("s1", bytes_out=5 * MB)
        pages = [
            ("adminpanel:dashboard", []), ("adminpanel:instructor_list", []), ("adminpanel:instructor_add", []),
            ("adminpanel:instructor_detail", [self.instructor.pk]), ("adminpanel:instructor_edit", [self.instructor.pk]),
            ("adminpanel:masterlist_upload", []), ("adminpanel:admin_list", []), ("adminpanel:admin_add", []),
            ("adminpanel:admin_edit", [self.admin.pk]), ("adminpanel:semester_list", []), ("adminpanel:sy_add", []),
            ("adminpanel:semester_add", []), ("adminpanel:semester_edit", [self.semester.pk]),
            ("adminpanel:policy_list", []), ("adminpanel:policy_add", []), ("adminpanel:policy_edit", [self.policy.pk]),
            ("adminpanel:monitoring", []), ("adminpanel:monitoring_live", []), ("adminpanel:reports", []),
            ("adminpanel:audit_logs", []),
        ]
        for name, args in pages:
            r = self.client.get(reverse(name, args=args))
            self.assertEqual(r.status_code, 200, name)

    def test_activate_page_renders(self):
        other = self.make_semester(active=False, semester=Semester.SECOND)
        self.assertEqual(self.client.get(reverse("adminpanel:semester_activate", args=[other.pk])).status_code, 200)

    def test_filters(self):
        s2, _ = self.make_instructor("s2")
        Instructor.objects.filter(pk=s2.pk).update(approval_status=Instructor.PENDING)
        s3, _ = self.make_instructor("s3", active=False)
        url = reverse("adminpanel:instructor_list")
        link = lambda i: 'href="/admin-panel/instructors/%d/"' % i.pk
        pending = self.client.get(url, {"registration": "pending"})
        self.assertContains(pending, link(s2))
        self.assertNotContains(pending, link(self.instructor))
        inactive = self.client.get(url, {"status": "inactive"})
        self.assertContains(inactive, link(s3))
        self.assertNotContains(inactive, link(self.instructor))
        self.assertContains(self.client.get(url, {"registration": "approved", "status": "active"}), link(self.instructor))

class InstructorManagementTests(AdminTestCase):
    def test_add_instructor_creates_account_and_shows_temp_password_once(self):
        r = self.client.post(reverse("adminpanel:instructor_add"), {
            "username": "INS-777", "first_name": "New", "last_name": "Teacher",
            "is_active": "on", "policy": self.policy.pk,
        }, follow=True)
        s = Instructor.objects.get(user__username="INS-777")
        self.assertTrue(s.user.must_change_password)
        self.assertIsNotNone(s.current_account)
        self.assertContains(r, "Temporary password")
        self.assertTrue(AuditLog.objects.filter(action="instructor.created", target="INS-777").exists())

    def test_duplicate_username_rejected(self):
        for name in ("s1", "S1"):  # case-insensitive
            r = self.client.post(reverse("adminpanel:instructor_add"), {
                "username": name, "first_name": "Dup", "last_name": "X", "is_active": "on",
            })
            self.assertContains(r, "already taken")
        self.assertEqual(Instructor.objects.filter(user__username__iexact="s1").count(), 1)

    def test_edit_instructor(self):
        r = self.client.post(reverse("adminpanel:instructor_edit", args=[self.instructor.pk]), {
            "username": "s1", "first_name": "Renamed", "last_name": "Tester", "is_active": "on",
        })
        self.assertEqual(r.status_code, 302)
        self.instructor.user.refresh_from_db()
        self.assertEqual(self.instructor.user.first_name, "Renamed")

    def test_deactivating_via_edit_kicks_the_instructor(self):
        sess = self.connect()
        self.client.post(reverse("adminpanel:instructor_edit", args=[self.instructor.pk]), {
            "username": "s1", "first_name": "S1", "last_name": "Tester",
        })  # is_active omitted -> account deactivated
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)
        self.instructor.user.refresh_from_db()
        self.assertFalse(self.instructor.user.is_active)

    def test_deactivate_disables_router_user_and_kicks_sessions(self):
        sess = self.connect()
        r = self.client.post(reverse("adminpanel:instructor_toggle_active", args=[self.instructor.pk]))
        self.assertEqual(r.status_code, 302)
        self.instructor.user.refresh_from_db()
        self.assertFalse(self.instructor.user.is_active)
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)
        self.assertEqual(sess.logout_reason, Session.ADMIN_KICK)
        with get_router() as router:
            self.assertTrue(router.state["users"]["s1"]["disabled"])
            self.assertEqual(router.active_sessions(), [])
        self.assertTrue(AuditLog.objects.filter(action="instructor.deactivated").exists())

    def test_deactivate_with_router_down_still_saves_and_warns(self):
        self.connect()
        self.router.set_offline(True)
        r = self.client.post(reverse("adminpanel:instructor_toggle_active", args=[self.instructor.pk]), follow=True)
        self.instructor.user.refresh_from_db()
        self.assertFalse(self.instructor.user.is_active)
        self.assertContains(r, "router could not be updated")

    def test_reactivate(self):
        self.instructor.user.is_active = False
        self.instructor.user.save()
        self.client.post(reverse("adminpanel:instructor_toggle_active", args=[self.instructor.pk]))
        self.instructor.user.refresh_from_db()
        self.assertTrue(self.instructor.user.is_active)

    def test_reset_password_sets_temp_and_flags_change(self):
        r = self.client.post(reverse("adminpanel:instructor_reset_password", args=[self.instructor.pk]), {"password": "Temp-1234"}, follow=True)
        self.instructor.user.refresh_from_db()
        self.assertTrue(self.instructor.user.check_password("Temp-1234"))
        self.assertTrue(self.instructor.user.must_change_password)
        self.assertContains(r, "Temp-1234")
        # blank => generated
        self.client.post(reverse("adminpanel:instructor_reset_password", args=[self.instructor.pk]), {"password": ""})
        self.instructor.user.refresh_from_db()
        self.assertFalse(self.instructor.user.check_password("Temp-1234"))

    def test_assign_policy_single(self):
        p2 = self.make_policy("Restricted", down=1, up=1, quota=500, default=False)
        self.client.post(reverse("adminpanel:instructor_assign_policy", args=[self.instructor.pk]), {"policy": p2.pk})
        self.account.refresh_from_db()
        self.assertEqual(self.account.policy, p2)

    def test_toggle_semester_account_disables_access(self):
        sess = self.connect()
        self.client.post(reverse("adminpanel:account_toggle", args=[self.account.pk]))
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)

    def test_bulk_actions(self):
        s2, _ = self.make_instructor("s2")
        s3, _ = self.make_instructor("s3")
        ids = [self.instructor.pk, s2.pk]
        url = reverse("adminpanel:instructor_bulk")
        self.client.post(url, {"action": "disable", "ids": ids})
        self.assertEqual(User.objects.filter(is_instructor=True, is_active=False).count(), 2)
        self.client.post(url, {"action": "enable", "ids": ids})
        self.assertEqual(User.objects.filter(is_instructor=True, is_active=False).count(), 0)
        p2 = self.make_policy("Restricted", quota=500, default=False)
        self.client.post(url, {"action": "policy", "policy": p2.pk, "ids": ids})
        self.assertEqual(SemesterAccount.objects.filter(policy=p2).count(), 2)
        self.assertEqual(SemesterAccount.objects.get(instructor=s3).policy, self.policy)  # untouched

    def test_bulk_disable_kicks_active_sessions(self):
        sess = self.connect()
        self.client.post(reverse("adminpanel:instructor_bulk"), {"action": "disable", "ids": [self.instructor.pk]})
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)

    def test_bulk_with_nothing_selected(self):
        r = self.client.post(reverse("adminpanel:instructor_bulk"), {"action": "disable"}, follow=True)
        self.assertContains(r, "Select at least one")

    def test_actions_require_post(self):
        for name in ("instructor_toggle_active", "instructor_reset_password", "instructor_assign_policy"):
            self.assertEqual(self.client.get(reverse(f"adminpanel:{name}", args=[self.instructor.pk])).status_code, 405)


class MasterlistViewTests(AdminTestCase):
    def upload(self, body):
        return self.client.post(reverse("adminpanel:masterlist_upload"),
                                {"file": SimpleUploadedFile("m.csv", (HEADER + body).encode())}, follow=True)

    def test_full_flow_preview_confirm_credentials(self):
        r = self.upload("2026-901,Ann,,Lee,\n2026-902,Bob,,Ray,\n,Bad,,Row,\ns1,Existing,,Tester,\n")
        self.assertContains(r, "Preview")
        self.assertContains(r, "Missing username")
        self.assertFalse(Instructor.objects.filter(user__username="2026-901").exists())  # nothing saved yet
        r = self.client.post(reverse("adminpanel:masterlist_confirm"))
        self.assertContains(r, "Import complete")
        self.assertTrue(Instructor.objects.filter(user__username="2026-901").exists())
        self.assertEqual(User.objects.get(username="s1").first_name, "Existing")
        # credentials sheet
        sheet = self.client.get(reverse("adminpanel:credentials_sheet"))
        rows = list(csv.DictReader(io.StringIO(sheet.content.decode())))
        self.assertEqual({r["username"] for r in rows}, {"2026-901", "2026-902"})
        pw = next(r["password"] for r in rows if r["username"] == "2026-901")
        self.assertTrue(User.objects.get(username="2026-901").check_password(pw))
        self.assertEqual(self.client.get(reverse("adminpanel:credentials_sheet") + "?format=xlsx").status_code, 200)
        self.client.post(reverse("adminpanel:credentials_clear"))
        self.assertEqual(self.client.get(reverse("adminpanel:credentials_sheet")).status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="masterlist.imported").exists())

    def test_bad_file_shows_error(self):
        r = self.client.post(reverse("adminpanel:masterlist_upload"), {"file": SimpleUploadedFile("m.txt", b"x")}, follow=True)
        self.assertContains(r, "Unsupported file type")

    def test_confirm_without_upload_redirects(self):
        r = self.client.post(reverse("adminpanel:masterlist_confirm"))
        self.assertRedirects(r, reverse("adminpanel:masterlist_upload"))

    def test_templates_download(self):
        r = self.client.get(reverse("adminpanel:masterlist_template"))
        self.assertIn("username,first_name", r.content.decode())
        self.assertEqual(self.client.get(reverse("adminpanel:masterlist_template") + "?format=xlsx").status_code, 200)

    def test_credentials_require_login_and_admin(self):
        self.client.logout()
        self.client.login(username="s1", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("adminpanel:credentials_sheet")).status_code, 302)


class AdminAccountTests(AdminTestCase):
    def test_add_admin(self):
        r = self.client.post(reverse("adminpanel:admin_add"), {"username": "second", "password": "Secret-123", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username="second")
        self.assertTrue(u.is_admin and u.check_password("Secret-123"))

    def test_add_admin_requires_password(self):
        r = self.client.post(reverse("adminpanel:admin_add"), {"username": "second", "is_active": "on"})
        self.assertContains(r, "password is required")

    def test_cannot_deactivate_self(self):
        self.make_admin("other")
        self.client.post(reverse("adminpanel:admin_toggle_active", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_cannot_deactivate_last_active_admin(self):
        other = self.make_admin("other")
        self.client.post(reverse("adminpanel:admin_toggle_active", args=[other.pk]))  # fine: boss is still active
        other.refresh_from_db()
        self.assertFalse(other.is_active)
        # now boss is the only active admin; another admin session cannot deactivate boss either:
        third = self.make_admin("third")
        self.client.logout()
        self.client.login(username="third", password=PASSWORD)
        self.client.post(reverse("adminpanel:admin_toggle_active", args=[self.admin.pk]))  # boss: third still active -> allowed
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)
        # third is now the last one
        self.client.post(reverse("adminpanel:admin_toggle_active", args=[third.pk]))
        third.refresh_from_db()
        self.assertTrue(third.is_active)

    def test_edit_form_cannot_deactivate_self(self):
        self.make_admin("other")
        r = self.client.post(reverse("adminpanel:admin_edit", args=[self.admin.pk]), {"username": "boss"})  # is_active omitted
        self.assertContains(r, "cannot deactivate your own account")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_instructors_are_not_listed_as_admins(self):
        self.assertNotContains(self.client.get(reverse("adminpanel:admin_list")), 'class="mono">s1')


class SemesterAdminTests(AdminTestCase):
    def test_sy_and_semester_crud(self):
        self.client.post(reverse("adminpanel:sy_add"), {"sy": "2027-2028"})
        from academics.models import SchoolYear

        sy = SchoolYear.objects.get(sy="2027-2028")
        self.client.post(reverse("adminpanel:semester_add"), {"sy": sy.pk, "semester": "1st", "start_date": "2027-06-01", "end_date": "2027-10-01"})
        sem = Semester.objects.get(sy=sy)
        self.assertFalse(sem.is_active)  # is_active cannot be set via the form
        self.client.post(reverse("adminpanel:semester_delete", args=[sem.pk]))
        self.assertFalse(Semester.objects.filter(pk=sem.pk).exists())

    def test_cannot_delete_active_semester_or_one_with_accounts(self):
        self.client.post(reverse("adminpanel:semester_delete", args=[self.semester.pk]))
        self.assertTrue(Semester.objects.filter(pk=self.semester.pk).exists())

    def test_cannot_delete_school_year_with_semesters(self):
        self.client.post(reverse("adminpanel:sy_delete", args=[self.semester.sy.pk]))
        self.assertTrue(Semester.objects.filter(pk=self.semester.pk).exists())

    def test_activation_via_post_switches_and_logs(self):
        sess = self.connect()
        new = self.make_semester(active=False, semester=Semester.SECOND)
        self.client.post(reverse("adminpanel:semester_activate", args=[new.pk]))
        self.assertEqual(Semester.get_active(), new)
        sess.refresh_from_db()
        self.assertEqual(sess.logout_reason, Session.SEMESTER_ENDED)
        self.assertTrue(AuditLog.objects.filter(action="semester.activated").exists())
        self.assertContains(self.client.get(reverse("adminpanel:semester_list")), "Set active")


class PolicyAdminTests(AdminTestCase):
    def test_create_edit_and_sync(self):
        r = self.client.post(reverse("adminpanel:policy_add"), {
            "name": "Restricted", "max_speed_down_mbps": 1, "max_speed_up_mbps": 1, "quota_limit_mb": 500,
        })
        self.assertEqual(r.status_code, 302)
        p = BandwidthPolicy.objects.get(name="Restricted")
        self.assertEqual(p.mikrotik_profile_name, "cdk-restricted")
        self.assertEqual(p.sync_status, "never")
        self.client.post(reverse("adminpanel:policy_sync", args=[p.pk]))
        p.refresh_from_db()
        self.assertEqual(p.sync_status, "ok")
        with get_router() as router:
            self.assertEqual(router.state["profiles"]["cdk-restricted"]["rate-limit"], "1M/1M")
        # editing marks it as needing a re-sync
        self.client.post(reverse("adminpanel:policy_edit", args=[p.pk]), {"name": "Restricted", "max_speed_down_mbps": 3, "max_speed_up_mbps": 1, "quota_limit_mb": 500})
        p.refresh_from_db()
        self.assertEqual(p.sync_status, "never")

    def test_sync_with_router_down_records_failure(self):
        self.router.set_offline(True)
        r = self.client.post(reverse("adminpanel:policy_sync_all"), follow=True)
        self.assertContains(r, "Router unreachable")
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.sync_status, "failed")
        self.assertContains(self.client.get(reverse("adminpanel:policy_list")), "Failed")

    def test_cannot_delete_policy_in_use(self):
        self.client.post(reverse("adminpanel:policy_delete", args=[self.policy.pk]))
        self.assertTrue(BandwidthPolicy.objects.filter(pk=self.policy.pk).exists())

    def test_validation(self):
        r = self.client.post(reverse("adminpanel:policy_add"), {"name": "Bad", "max_speed_down_mbps": 0, "max_speed_up_mbps": 1})
        self.assertContains(r, "greater than zero")


class MonitoringTests(AdminTestCase):
    def test_live_view_lists_active_sessions_and_stats(self):
        self.connect()
        r = self.client.get(reverse("adminpanel:monitoring_live"))
        self.assertContains(r, "S1 Tester".replace("S1 Tester", "Tester, S1"))
        self.assertContains(r, "192.168.88.50")
        self.assertContains(r, "Kick")
        self.assertContains(r, "Online")
        cards = self.client.get(reverse("adminpanel:monitoring_live") + "?view=cards")
        self.assertNotContains(cards, "Active sessions")

    def test_kick(self):
        sess = self.connect()
        r = self.client.post(reverse("adminpanel:monitoring_kick"), {"active_id": sess.mikrotik_session_id, "user": "s1"})
        self.assertContains(r, "was disconnected")
        sess.refresh_from_db()
        self.assertEqual((sess.is_active, sess.logout_reason), (False, Session.ADMIN_KICK))
        self.assertTrue(AuditLog.objects.filter(action="session.kicked", target="s1").exists())

    def test_router_offline_is_shown_not_crashed(self):
        self.router.set_offline(True)
        r = self.client.get(reverse("adminpanel:monitoring_live"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Router OFFLINE")
        self.assertContains(r, "mock router set offline")
        self.assertEqual(self.client.get(reverse("adminpanel:dashboard")).status_code, 200)

    def test_non_django_router_users_have_no_kick_button(self):
        self.router.connect()
        self.router.inject_active("someone-else", "AA:AA:AA:AA:AA:AA", "192.168.88.99")
        r = self.client.get(reverse("adminpanel:monitoring_live"))
        self.assertContains(r, "someone-else")
        self.assertNotContains(r, "Kick")


class ReportAndLogTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        from django.utils import timezone

        for i, (name, mb) in enumerate([("s1", 100), ("s2", 300)]):
            st = self.instructor if name == "s1" else self.make_instructor(name)[0]
            Session.objects.create(instructor=st, semester_account=st.current_account, login_time=timezone.now(), logout_time=timezone.now(),
                                   is_active=False, consumption=mb, bytes_out=mb * MB, mac_address="AA:AA:AA:AA:AA:0%d" % i,
                                   ip_address="192.168.88.%d" % (20 + i), logout_reason=Session.USER)

    def test_report_totals_and_ranking(self):
        r = self.client.get(reverse("adminpanel:reports"))
        self.assertEqual(r.context["total_mb"], 400)
        self.assertEqual(r.context["per_instructor"][0]["username"], "s2")  # top consumer first
        self.assertNotContains(r, "Department")
        future = self.client.get(reverse("adminpanel:reports"), {"start": "2999-01-01", "end": "2999-01-02"})
        self.assertEqual(future.context["total_mb"], 0)

    def test_csv_exports(self):
        for kind, header in [("instructors", "username"), ("daily", "date"), ("sessions", "username")]:
            r = self.client.get(reverse("adminpanel:report_csv"), {"type": kind})
            self.assertEqual(r["Content-Type"], "text/csv", kind)
            self.assertTrue(r.content.decode().startswith(header), kind)
        body = self.client.get(reverse("adminpanel:report_csv"), {"type": "instructors"}).content.decode()
        self.assertIn("s2", body)

    def test_pdf_export(self):
        r = self.client.get(reverse("adminpanel:report_pdf"))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_bad_dates_fall_back_to_defaults(self):
        self.assertEqual(self.client.get(reverse("adminpanel:reports"), {"start": "garbage", "end": "2026-13-45"}).status_code, 200)

    def test_audit_log_filters(self):
        self.client.post(reverse("adminpanel:instructor_reset_password", args=[self.instructor.pk]), {"password": "Temp-1234"})
        r = self.client.get(reverse("adminpanel:audit_logs"), {"action": "instructor.password_reset"})
        self.assertEqual([l.action for l in r.context["logs"]], ["instructor.password_reset"])
        self.assertEqual(r.context["logs"][0].target, "s1")
        self.assertEqual(r.context["logs"][0].actor, self.admin)
        r = self.client.get(reverse("adminpanel:audit_logs"), {"actor": "nobody"})
        self.assertEqual(len(r.context["logs"]), 0)
        self.assertEqual(self.client.get(reverse("adminpanel:audit_logs"), {"start": "2000-01-01", "end": "2100-01-01", "q": "S1"}).status_code, 200)

    def test_report_export_is_audited(self):
        self.client.get(reverse("adminpanel:report_csv"))
        self.assertTrue(AuditLog.objects.filter(action="report.exported").exists())


class FastTrackWarningTests(AdminTestCase):
    def test_warning_shown_only_while_fasttrack_is_enabled(self):
        url = reverse("adminpanel:monitoring_live")
        self.assertNotContains(self.client.get(url), "Speed limits may not be enforced")
        self.router.set_fasttrack(True)
        r = self.client.get(url)
        self.assertContains(r, "Speed limits may not be enforced")
        self.assertContains(r, "fasttrack-connection")
        self.router.set_fasttrack(False)
        self.assertNotContains(self.client.get(url), "Speed limits may not be enforced")

    def test_check_router_command_warns(self):
        from io import StringIO

        from django.core.management import call_command

        self.router.set_fasttrack(True)
        out = StringIO()
        call_command("check_router", stdout=out)
        self.assertIn("FastTrack is ENABLED", out.getvalue())
        self.router.set_fasttrack(False)
        out = StringIO()
        call_command("check_router", stdout=out)
        self.assertIn("FastTrack: off", out.getvalue())


class UsernameLabelAdminTests(AdminTestCase):
    def test_admin_screens_use_username_and_have_no_department(self):
        for name in ("adminpanel:instructor_add", "adminpanel:instructor_list", "adminpanel:reports"):
            r = self.client.get(reverse(name))
            self.assertContains(r, "Username", msg_prefix=name)
            self.assertNotContains(r, "ID no", msg_prefix=name)
            self.assertNotContains(r, "Department", msg_prefix=name)
