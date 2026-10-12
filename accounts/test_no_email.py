"""The system collects no e-mail addresses: the field is gone and nothing needs it."""
from io import StringIO

from django.core.management import call_command
from django.urls import reverse

from accounts.models import User
from config.testutils import PASSWORD, BaseTestCase


class NoEmailTests(BaseTestCase):
    def test_user_model_has_no_email_field(self):
        self.assertNotIn("email", [f.name for f in User._meta.get_fields()])

    def test_create_user_still_accepts_the_old_positional_email_argument(self):
        user = User.objects.create_user("legacy1", "someone@example.com", "pw-12345")
        self.assertIsNone(getattr(user, "email", None))  # no value is stored
        self.assertTrue(user.check_password("pw-12345"))

    def test_create_superuser_without_email(self):
        user = User.objects.create_superuser("root1", password="pw-12345")
        self.assertTrue(user.is_superuser and user.is_staff)

    def test_createsuperuser_command_does_not_ask_for_email(self):
        call_command("createsuperuser", interactive=False, username="root2", verbosity=0)
        self.assertTrue(User.objects.filter(username="root2", is_superuser=True).exists())

    def test_create_admin_command(self):
        call_command("create_admin", "--username", "boss2", "--password", "Str0ng-pass!", stdout=StringIO())
        self.assertTrue(User.objects.get(username="boss2").is_admin)

    def test_create_admin_defaults_to_a_superuser_and_staff_flag_makes_a_staff_account(self):
        call_command("create_admin", "--username", "chief2", "--password", "Str0ng-pass!", stdout=StringIO())
        chief = User.objects.get(username="chief2")
        self.assertTrue(chief.is_admin and chief.is_superuser and chief.is_staff)
        self.assertEqual(chief.role, "superuser")
        call_command("create_admin", "--username", "worker2", "--password", "Str0ng-pass!", "--staff", stdout=StringIO())
        worker = User.objects.get(username="worker2")
        self.assertTrue(worker.is_admin)
        self.assertFalse(worker.is_superuser or worker.is_staff)
        self.assertEqual(worker.role, "staff")

    def test_roles_and_the_superuser_invariant(self):
        self.assertEqual(User.objects.create_user("i1", password="pw-12345", is_instructor=True).role, "instructor")
        self.assertEqual(User.objects.create_user("s9", password="pw-12345", is_admin=True).role, "staff")
        plain = User.objects.create_user("p1", password="pw-12345")
        plain.is_superuser = True
        plain.save(update_fields=["is_superuser"])  # e.g. toggled in Django admin: the portal flags follow
        plain.refresh_from_db()
        self.assertTrue(plain.is_admin and plain.is_staff)
        self.assertEqual(plain.role, "superuser")
        self.assertEqual(User.objects.create_superuser("root9", password="pw-12345").is_admin, True)  # createsuperuser works in the panel too

    def test_model_validation_works_without_email(self):
        User(username="valid1").full_clean(exclude=["password"])

    def test_django_admin_pages_render_for_a_superuser(self):
        User.objects.create_superuser("root3", password=PASSWORD)
        self.client.login(username="root3", password=PASSWORD)
        self.assertEqual(self.client.get("/django-admin/accounts/user/").status_code, 200)
        self.assertEqual(self.client.get("/django-admin/accounts/user/add/").status_code, 200)
        self.assertEqual(self.client.get("/django-admin/accounts/user/%d/change/" % User.objects.get(username="root3").pk).status_code, 200)

    def test_admin_account_form_has_no_email(self):
        self.make_admin("boss", is_superuser=True)
        self.client.login(username="boss", password=PASSWORD)
        r = self.client.get(reverse("adminpanel:admin_add"))
        self.assertNotContains(r, "Email")
        self.assertNotContains(self.client.get(reverse("adminpanel:instructor_add")), "Email")
        self.assertNotContains(self.client.get(reverse("adminpanel:admin_list")), "Email")
