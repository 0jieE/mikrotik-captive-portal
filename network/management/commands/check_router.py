from django.conf import settings
from django.core.management.base import BaseCommand

from network.mikrotik import RouterError, get_router


class Command(BaseCommand):
    help = "Print RouterOS API connectivity, version and hotspot server status."

    def handle(self, *args, **opts):
        mode = "MOCK" if settings.MIKROTIK_MOCK else "REAL"
        self.stdout.write(f"Mode: {mode}   Target: {settings.MIKROTIK_HOST}:{settings.MIKROTIK_PORT}   API user: {settings.MIKROTIK_USER}")
        try:
            with get_router() as r:
                self.stdout.write(self.style.SUCCESS("API connection: OK"))
                res = r.resources()
                self.stdout.write(f"Board: {res['board']}   RouterOS: {res['version']}   Uptime: {res['uptime']}   CPU: {res['cpu_load']}%")
                hs = r.server_status()
                if hs["found"]:
                    state = "DISABLED" if hs["disabled"] else "running"
                    self.stdout.write(f"Hotspot server '{hs['name']}': {state} (interface {hs['interface']}, profile {hs['profile']})")
                else:
                    self.stdout.write(self.style.WARNING(f"Hotspot server '{hs['name']}' NOT FOUND on the router"))
                try:
                    if r.fasttrack_active():
                        self.stdout.write(self.style.WARNING(
                            "WARNING: FastTrack is ENABLED on the router - it bypasses queues, so bandwidth limits are NOT enforced.\n"
                            "         Fix: /ip firewall filter disable [find action=fasttrack-connection]   (see docs/MIKROTIK_SETUP.md)"
                        ))
                    else:
                        self.stdout.write("FastTrack: off (bandwidth limits can be enforced)")
                except RouterError as e:
                    self.stdout.write(self.style.WARNING(f"Could not check FastTrack: {e}"))
                self.stdout.write(f"Active hotspot sessions: {len(r.active_sessions())}")
                self.stdout.write(f"cdk-managed hotspot users: {len(r.list_managed_users())}")
        except RouterError as e:
            self.stdout.write(self.style.ERROR(f"API connection FAILED: {e}"))
            raise SystemExit(1)
