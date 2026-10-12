import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from network.mikrotik import MockMikroTik


class Command(BaseCommand):
    help = "Demo helper (mock router only): add random traffic to every active hotspot session."

    def add_arguments(self, parser):
        parser.add_argument("--mb", type=float, default=25.0, help="Approx. MB to add per session")
        parser.add_argument("--user", help="Only this username")

    def handle(self, *args, **opts):
        if not settings.MIKROTIK_MOCK:
            raise CommandError("mock_traffic only works with MIKROTIK_MOCK=True")
        mock = MockMikroTik()
        with mock as r:
            actives = r.active_sessions()
        n = 0
        for a in actives:
            if opts["user"] and a["user"] != opts["user"]:
                continue
            total = int(opts["mb"] * 1024 * 1024 * random.uniform(0.5, 1.5))
            mock.add_traffic(a["user"], bytes_in=int(total * 0.15), bytes_out=int(total * 0.85))
            n += 1
        self.stdout.write(f"Added traffic to {n} session(s).")
