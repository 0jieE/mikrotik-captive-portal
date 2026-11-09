from django.urls import reverse

from config.testutils import PASSWORD, BaseTestCase


class UserManualTests(BaseTestCase):
    def test_instructor_guide_is_public(self):
        r = self.client.get(reverse("portal:help"))
        self.assertContains(r, "How the instructor Wi-Fi works")
        self.assertContains(r, "http://testserver/portal/")  # the dashboard address to bookmark

    def test_login_page_links_to_the_guide(self):
        self.assertContains(self.client.get(reverse("portal:login")), reverse("portal:help"))

    def test_instructor_sees_help_tab(self):
        self.basic_setup()
        self.client.login(username="s1", password=PASSWORD)
        self.assertContains(self.client.get(reverse("portal:dashboard")), reverse("portal:help"))

    def test_staff_manual_needs_a_staff_login(self):
        url = reverse("adminpanel:help")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.basic_setup()
        self.client.login(username="s1", password=PASSWORD)
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_staff_and_superuser_can_read_the_staff_manual(self):
        for name, extra in (("staff1", {}), ("root1", {"is_superuser": True})):
            self.make_admin(username=name, **extra)
            self.client.login(username=name, password=PASSWORD)
            r = self.client.get(reverse("adminpanel:help"))
            self.assertContains(r, "Start of a semester")
            self.assertContains(r, "User manual")
            self.client.logout()
