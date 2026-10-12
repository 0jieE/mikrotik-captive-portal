from django.core.management.base import BaseCommand

from network.mikrotik import RouterError
from network.services import sync_router_full


class Command(BaseCommand):
    help = "One-shot: push all policies and active accounts to MikroTik; disable router users that Django says should be off."

    def handle(self, *args, **opts):
        try:
            s = sync_router_full()
        except RouterError as e:
            self.stdout.write(self.style.ERROR(f"Router unreachable: {e}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(
            f"Policies synced: {s['policies']}  Users pushed: {s['users_pushed']}  Users disabled: {s['users_disabled']}"
        ))
        for err in s["errors"]:
            self.stdout.write(self.style.WARNING(f"  ! {err}"))
