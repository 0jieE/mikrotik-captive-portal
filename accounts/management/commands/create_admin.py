import getpass

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = (
        "Create (or reset) a portal account that can sign in to /admin-panel/. By default it is a SUPERUSER "
        "(admin panel + Django admin); use --staff for a staff account (admin panel only). "
        "Use this instead of `createsuperuser` - it sets the portal's flags."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", help="Login name (prompted if omitted)")
        parser.add_argument("--password", help="Password (prompted if omitted; avoid on shared terminals)")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument(
            "--staff", "--no-superuser", dest="staff", action="store_true",
            help="Create a STAFF account: admin panel only, no Django /django-admin/ access",
        )

    def handle(self, *args, **opts):
        username = (opts["username"] or input("Admin username: ")).strip()
        if not username:
            raise CommandError("A username is required.")
        password = opts["password"]
        if not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords did not match.")
        if len(password) < 8:
            raise CommandError("Use a password of at least 8 characters.")

        user, created = User.objects.get_or_create(username=username)
        if not created and user.is_instructor:
            raise CommandError(f"'{username}' is an instructor account. Choose a different username.")
        user.is_admin, user.is_active, user.is_instructor = True, True, False
        user.is_staff = user.is_superuser = not opts["staff"]
        user.must_change_password = False
        for field in ("first_name", "last_name"):
            if opts[field]:
                setattr(user, field, opts[field])
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f"{user.role_label} '{username}' {'created' if created else 'updated'}."
            + ("" if user.is_superuser else " (staff: no access to Django admin)")
        ))
