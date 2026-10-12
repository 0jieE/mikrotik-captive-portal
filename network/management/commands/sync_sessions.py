import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from network.mikrotik import RouterError
from network.sync import run_and_record


class Command(BaseCommand):
    help = "Loop (default every 30 s): update open Sessions from /ip/hotspot/active, close vanished ones, enforce quota."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=30, help="Seconds between runs (default 30)")
        parser.add_argument("--once", action="store_true", help="Run a single pass and exit")

    def handle(self, *args, **opts):
        interval = opts["interval"]
        self.stdout.write(f"sync_sessions started (interval {interval}s). Ctrl+C to stop.")
        while True:
            stamp = timezone.localtime().strftime("%H:%M:%S")
            try:
                s = run_and_record()
                self.stdout.write(f"[{stamp}] updated={s['updated']} closed={s['closed']} kicked={s['kicked']} adopted={s['adopted']}")
            except RouterError as e:
                self.stdout.write(self.style.WARNING(f"[{stamp}] router unavailable: {e}"))
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                self.stdout.write(self.style.ERROR(f"[{stamp}] unexpected error: {e!r}"))
            if opts["once"]:
                return
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write("Stopped.")
                return
