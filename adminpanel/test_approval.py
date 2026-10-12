"""Administrator side of self-registration: see pending requests, approve or reject them."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from academics.models import Semester
from accounts.models import AuditLog
from config.testutils import PASSWORD, BaseTestCase
from instructors.models import Instructor, SemesterAccount
from network.mikrotik import get_router
from network.models import Session
from network.services import connect_instructor, evaluate_access
from network.sync import sync_sessions_once


class ApprovalTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.basic_setup()
        self.admin = self.make_admin()
        self.client.login(username="boss", password=PASSWORD)
        self.pending = self.make_pending("p1")
        self.pending2 = self.make_pending("p2")

    def make_pending(self, username):
        i, _ = self.make_instructor(username, account=False)
        i.approval_status = Instructor.PENDING
        i.save()
        return i


class ApprovalListAndBadgeTests(ApprovalTestCase):
    def test_registration_filter_and_row_actions(self):
        url = reverse("adminpanel:instructor_list")
        link = lambda i: 'href="/admin-panel/instructors/%d/"' % i.pk
        r = self.client.get(url, {"registration": "pending"})
        self.assertContains(r, link(self.pending))
        self.assertContains(r, link(self.pending2))
        self.assertNotContains(r, link(self.instructor))
        self.assertContains(r, "Pending")
        self.assertContains(r, reverse("adminpanel:instructor_approve", args=[self.pending.pk]))  # quick-approve button
        approved = self.client.get(url, {"registration": "approved"})
        self.assertContains(approved, link(self.instructor))
        self.assertNotContains(approved, link(self.pending))

    def test_sidebar_badge_and_dashboard_banner_show_the_pending_count(self):
        dash = self.client.get(reverse("adminpanel:dashboard"))
        self.assertContains(dash, "2 instructor registrations")
        self.assertContains(dash, "registration=pending")
        self.assertContains(dash, 'class="sb-badge"')
        self.assertEqual(dash.context["pending_registrations"], 2)

    def test_no_badge_when_nothing_is_pending(self):
        Instructor.objects.filter(approval_status=Instructor.PENDING).update(approval_status=Instructor.APPROVED)
        dash = self.client.get(reverse("adminpanel:dashboard"))
        self.assertNotContains(dash, 'class="sb-badge"')
        self.assertNotContains(dash, "waiting for your approval")

    def test_badge_is_not_computed_for_non_admins(self):
        self.client.logout()
        self.client.login(username="s1", password=PASSWORD)
        r = self.client.get(reverse("portal:dashboard"))
        self.assertNotIn("pending_registrations", r.context)

    def test_detail_page_shows_the_approval_card_only_when_needed(self):
        self.assertContains(self.client.get(reverse("adminpanel:instructor_detail", args=[self.pending.pk])), "Registration waiting for approval")
        self.assertNotContains(self.client.get(reverse("adminpanel:instructor_detail", args=[self.instructor.pk])), "Registration waiting for approval")


class ApproveRejectTests(ApprovalTestCase):
    def test_approve_creates_an_account_with_the_chosen_policy_and_records_who_and_when(self):
        p2 = self.make_policy("Restricted", quota=500, default=False)
        r = self.client.post(reverse("adminpanel:instructor_approve", args=[self.pending.pk]), {"policy": p2.pk}, follow=True)
        self.assertContains(r, "approved")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Instructor.APPROVED)
        self.assertEqual(self.pending.reviewed_by, self.admin)
        self.assertIsNotNone(self.pending.reviewed_at)
        self.assertEqual(SemesterAccount.objects.get(instructor=self.pending).policy, p2)
        self.assertTrue(AuditLog.objects.filter(action="instructor.approved", target=self.pending.user.username).exists())
        self.assertTrue(evaluate_access(self.pending.user).allowed)

    def test_approve_uses_the_default_policy_when_none_is_chosen(self):
        self.client.post(reverse("adminpanel:instructor_approve", args=[self.pending.pk]))
        self.assertEqual(SemesterAccount.objects.get(instructor=self.pending).policy, self.policy)

    def test_approve_without_an_active_semester_warns_and_creates_no_account(self):
        Semester.objects.update(is_active=False)
        r = self.client.post(reverse("adminpanel:instructor_approve", args=[self.pending.pk]), follow=True)
        self.assertContains(r, "no active semester")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Instructor.APPROVED)
        self.assertFalse(SemesterAccount.objects.filter(instructor=self.pending).exists())

    def test_quick_approve_returns_to_the_filtered_list(self):
        url = reverse("adminpanel:instructor_list") + "?registration=pending"
        r = self.client.post(reverse("adminpanel:instructor_approve", args=[self.pending.pk]), {"next": url})
        self.assertRedirects(r, url, fetch_redirect_response=False)
        evil = self.client.post(reverse("adminpanel:instructor_approve", args=[self.pending2.pk]), {"next": "https://evil.example/"})
        self.assertRedirects(evil, reverse("adminpanel:instructor_detail", args=[self.pending2.pk]), fetch_redirect_response=False)

    def test_reject(self):
        r = self.client.post(reverse("adminpanel:instructor_reject", args=[self.pending.pk]), follow=True)
        self.assertContains(r, "rejected")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Instructor.REJECTED)
        self.assertEqual(self.pending.reviewed_by, self.admin)
        self.assertEqual(evaluate_access(self.pending.user).code, "rejected")
        self.assertTrue(AuditLog.objects.filter(action="instructor.rejected").exists())

    def test_a_rejected_registration_can_still_be_approved_later(self):
        self.client.post(reverse("adminpanel:instructor_reject", args=[self.pending.pk]))
        self.client.post(reverse("adminpanel:instructor_approve", args=[self.pending.pk]))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Instructor.APPROVED)

    def test_only_pending_registrations_can_be_rejected(self):
        r = self.client.post(reverse("adminpanel:instructor_reject", args=[self.instructor.pk]), follow=True)
        self.assertContains(r, "Only a pending registration")
        self.instructor.refresh_from_db()
        self.assertEqual(self.instructor.approval_status, Instructor.APPROVED)

    def test_actions_need_post_and_an_admin(self):
        self.assertEqual(self.client.get(reverse("adminpanel:instructor_approve", args=[self.pending.pk])).status_code, 405)
        self.client.logout()
        self.client.login(username="s1", password=PASSWORD)
        self.client.post(reverse("adminpanel:instructor_approve", args=[self.pending.pk]))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Instructor.PENDING)

    def test_bulk_approve_and_reject(self):
        url = reverse("adminpanel:instructor_bulk")
        self.client.post(url, {"action": "approve", "ids": [self.pending.pk]})
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Instructor.APPROVED)
        self.assertTrue(SemesterAccount.objects.filter(instructor=self.pending).exists())
        self.client.post(url, {"action": "reject", "ids": [self.pending.pk, self.pending2.pk, self.instructor.pk]})
        self.pending.refresh_from_db(); self.pending2.refresh_from_db(); self.instructor.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Instructor.APPROVED)   # was already approved: untouched
        self.assertEqual(self.instructor.approval_status, Instructor.APPROVED)
        self.assertEqual(self.pending2.approval_status, Instructor.REJECTED)  # only the pending one

    def test_admin_added_and_imported_instructors_are_approved_at_once(self):
        self.client.post(reverse("adminpanel:instructor_add"), {
            "username": "ADD-1", "first_name": "A", "last_name": "B", "is_active": "on"})
        self.assertEqual(Instructor.objects.get(user__username="ADD-1").approval_status, Instructor.APPROVED)
        body = "username,first_name,middle_name,last_name,extension_name" + chr(10) + "P1,Pending,,One," + chr(10)
        self.client.post(reverse("adminpanel:masterlist_upload"), {"file": SimpleUploadedFile("m.csv", body.encode())})
        self.client.post(reverse("adminpanel:masterlist_confirm"))
        self.pending.refresh_from_db()
        self.assertEqual(Instructor.objects.get(user__username="P1").approval_status, Instructor.APPROVED)


class ApprovalAccessRuleTests(ApprovalTestCase):
    MAC, IP = "AA:BB:CC:DD:EE:01", "192.168.88.50"

    def test_access_codes(self):
        self.assertEqual(evaluate_access(self.pending.user).code, "pending_approval")
        self.assertIn("waiting for approval", evaluate_access(self.pending.user).message)
        Instructor.objects.filter(pk=self.pending.pk).update(approval_status=Instructor.REJECTED)
        fresh = Instructor.objects.get(pk=self.pending.pk)  # evaluate_access reads the related object, so reload it
        self.assertEqual(evaluate_access(fresh.user).code, "rejected")
        self.assertTrue(evaluate_access(self.instructor.user).allowed)

    def test_pending_instructor_never_reaches_the_router(self):
        # even if an account row exists, an unapproved registration is refused
        SemesterAccount.objects.create(instructor=self.pending, semester=self.semester, policy=self.policy)
        res = connect_instructor(self.pending.user, self.MAC, self.IP)
        self.assertFalse(res.success)
        self.assertEqual(res.code, "pending_approval")
        with get_router() as r:
            self.assertEqual(r.active_sessions(), [])
            self.assertEqual(r.list_managed_users(), [])

    def test_rejecting_an_instructor_who_is_online_gets_them_kicked_by_the_sync(self):
        sess = connect_instructor(self.instructor.user, self.MAC, self.IP).session
        Instructor.objects.filter(pk=self.instructor.pk).update(approval_status=Instructor.REJECTED)
        sync_sessions_once()
        sess.refresh_from_db()
        self.assertFalse(sess.is_active)
        self.assertEqual(sess.logout_reason, Session.ADMIN_KICK)

    def test_demo_seed_includes_one_pending_registration(self):
        from django.core.management import call_command

        call_command("seed_demo", verbosity=0)
        i = Instructor.objects.get(user__username="INS-0011")
        self.assertEqual(i.approval_status, Instructor.PENDING)
        self.assertFalse(SemesterAccount.objects.filter(instructor=i).exists())
