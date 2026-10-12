from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CDK Wi-Fi</title>
<meta http-equiv="pragma" content="no-cache">
<meta http-equiv="expires" content="-1">
<meta http-equiv="refresh" content="0; url={url}">
<style>body{{font-family:sans-serif;text-align:center;padding:3em 1em;color:#17603a}}</style>
</head>
<body>
<p>Opening the CDK Wi-Fi login page&hellip;</p>
<p><a href="{url}">Tap here if you are not redirected</a></p>
<script>window.location.replace("{url}");</script>
</body>
</html>
"""


STATUS_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CDK Wi-Fi</title>
<meta http-equiv="refresh" content="0; url={url}">
<style>body{{font-family:sans-serif;text-align:center;padding:3em 1em;color:#17603a}}</style>
</head>
<body>
<p>Opening your Wi-Fi dashboard&hellip;</p>
<p><a href="{url}">Tap here if you are not redirected</a></p>
<script>window.location.replace("{url}");</script>
</body>
</html>
"""


class Command(BaseCommand):
    help = (
        "Generate mikrotik/hotspot/login.html (redirect to the Django login) and status.html (redirect to the "
        "instructor dashboard) from PORTAL_BASE_URL."
    )

    def handle(self, *args, **opts):
        base = settings.PORTAL_BASE_URL.rstrip("/")
        # $(mac), $(ip) and $(link-orig-esc) are filled in by the MikroTik hotspot when it serves the page.
        url = f"{base}/portal/login/?mac=$(mac)&amp;ip=$(ip)&amp;dst=$(link-orig-esc)"
        js_url = url.replace("&amp;", "&")
        html = TEMPLATE.format(url=url).replace(f'window.location.replace("{url}")', f'window.location.replace("{js_url}")')
        out = Path(settings.BASE_DIR) / "mikrotik" / "hotspot" / "login.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8", newline="\n")
        self.stdout.write(self.style.SUCCESS(f"Wrote {out}"))
        self.stdout.write(f"  login.html  redirects to: {js_url}")

        # Shown by the router when an already-connected instructor opens the hotspot address (e.g. login.cdk.local).
        dash = f"{base}/portal/"
        status = out.parent / "status.html"
        status.write_text(STATUS_TEMPLATE.format(url=dash), encoding="utf-8", newline="\n")
        self.stdout.write(self.style.SUCCESS(f"Wrote {status}"))
        self.stdout.write(f"  status.html redirects to: {dash}")
        self.stdout.write("Upload both to the router (WinBox -> Files -> hotspot/). See docs/MIKROTIK_SETUP.md.")
