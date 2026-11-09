"""Admin (portal admin) and Superuser accounts are created and edited only by superusers, in the Django admin."""
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, User
from config.testutils import PASSWORD, BaseTestCase

ADD_URL = "/django-admin/accounts/user/add/"
NEW_PASSWORD = "Secret-pass-123"


def change_url(user):
    return f"/django-admin/accounts/user/{user.pk}/change/"


class DjangoAdminAccountTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.make_admin("root", is_superuser=True)
        self.client.login(username="root", password=PASSWORD)

    def add(self, username, role, password=NEW_PASSWORD):
        return self.client.post(ADD_URL, {"username": username, "first_name": "New", "last_name": "Person", "role": role,
                                          "password1": password, "password2": password})

    def edit(self, user, **changes):
        joined = timezone.localtime(user.date_joined)
        data = {"username": user.username, "first_name": user.first_name, "middle_name": "", "last_name": user.last_name,
                "extension_name": "", "role": user.role, "is_active": "on" if user.is_active else "",
                "date_joined_0": joined.strftime("%Y-%m-%d"), "date_joined_1": joined.strftime("%H:%M:%S")}
        data.update(changes)
        return self.client.post(change_url(user), {k: v for k, v in data.items() if v != ""})

    def test_the_old_admin_accounts_page_is_gone(self):
        self.assertEqual(self.client.get("/admin-panel/admins/").status_code, 404)
        self.assertNotContains(self.client.get(reverse("adminpanel:dashboard")), "Admin accounts")

    def test_superuser_creates_a_portal_admin_without_django_admin_access(self):
        r = self.add("second", User.STAFF)
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username="second")
        self.assertTrue(u.is_admin and u.check_password(NEW_PASSWORD) and u.must_change_password)
        self.assertFalse(u.is_superuser or u.is_staff or u.is_instructor)
        self.assertEqual(u.role_label, "Admin")
        self.assertTrue(AuditLog.objects.filter(action="admin.created", target="second").exists())
        User.objects.filter(pk=u.pk).update(must_change_password=False)
        self.client.logout()
        self.client.login(username="second", password=NEW_PASSWORD)
        self.assertEqual(self.client.get(reverse("adminpanel:dashboard")).status_code, 200)
        self.assertEqual(self.client.get("/django-admin/").status_code, 302)  # sent away from Django admin

    def test_superuser_creates_another_superuser(self):
        self.assertEqual(self.add("chief", User.SUPERUSER).status_code, 302)
        u = User.objects.get(username="chief")
        self.assertTrue(u.is_admin and u.is_superuser and u.is_staff)
        User.objects.filter(pk=u.pk).update(must_change_password=False)
        self.client.logout()
        self.client.login(username="chief", password=NEW_PASSWORD)
        self.assertEqual(self.client.get("/django-admin/").status_code, 200)

    def test_add_page_offers_only_admin_and_superuser(self):
        page = self.client.get(ADD_URL)
        self.assertContains(page, "Admin (portal admin)")
        self.assertContains(page, "Superuser - the portal and the Django admin")
        self.assertNotContains(page, 'name="is_superuser"')
        r = self.add("weird", "instructor")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="weird").exists())

    def test_portal_admin_cannot_open_django_admin_or_create_accounts(self):
        self.make_admin("worker")
        self.client.logout()
        self.client.login(username="worker", password=PASSWORD)
        self.assertEqual(self.client.get(ADD_URL).status_code, 302)
        self.add("sneaky", User.SUPERUSER)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_change_admin_to_superuser_and_back(self):
        worker = self.make_admin("worker", first_name="W", last_name="K")
        self.assertEqual(self.edit(worker, role=User.SUPERUSER).status_code, 302)
        worker.refresh_from_db()
        self.assertTrue(worker.is_superuser and worker.is_staff)
        self.edit(worker, role=User.STAFF)
        worker.refresh_from_db()
        self.assertFalse(worker.is_superuser or worker.is_staff)
        self.assertTrue(worker.is_admin)

    def test_cannot_remove_your_own_superuser_role(self):
        self.make_admin("chief2", is_superuser=True)
        r = self.edit(self.root, role=User.STAFF)
        self.assertContains(r, "You cannot remove your own superuser role")
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_superuser)

    def test_cannot_deactivate_yourself_and_last_superuser_stays(self):
        other = self.make_admin("chief2", is_superuser=True)
        self.edit(other, role=User.STAFF)  # allowed: root is still a superuser
        other.refresh_from_db()
        self.assertFalse(other.is_superuser)
        r = self.edit(self.root, is_active="")
        self.assertContains(r, "You cannot deactivate your own account")
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_active)

    def test_last_superuser_rules(self):
        from accounts.roles import deactivation_block_reason, demotion_block_reason

        worker = self.make_admin("worker")
        self.assertEqual(demotion_block_reason(worker, self.root, User.STAFF), "There must always be at least one active superuser.")
        self.assertEqual(deactivation_block_reason(worker, self.root), "You cannot deactivate the last active superuser.")
        self.assertIsNone(deactivation_block_reason(self.root, worker))

    def test_cannot_delete_yourself_and_no_bulk_delete(self):
        self.assertEqual(self.client.get(f"/django-admin/accounts/user/{self.root.pk}/delete/").status_code, 403)
        self.assertNotIn("delete_selected", self.client.get("/django-admin/accounts/user/").content.decode())

    def test_instructor_change_page_has_no_role_field(self):
        self.make_policy()
        self.make_semester()
        inst, _ = self.make_instructor()
        page = self.client.get(change_url(inst.user))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="role"')

    def test_user_list_shows_roles(self):
        self.make_admin("worker")
        page = self.client.get("/django-admin/accounts/user/")
        self.assertContains(page, "Superuser")
        self.assertContains(page, "Admin")
