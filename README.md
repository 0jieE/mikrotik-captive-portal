# CDK Instructor Internet Access & Bandwidth Control System

Django 5 web app that controls instructor Wi-Fi access through a MikroTik Hotspot
(Colegio de Kidapawan, Inc.). **Django is the source of truth**; the router only mirrors what Django decides
(users, profiles, speed limits, quota) through the RouterOS API (`librouteros`, port 8728).

* Admin panel at `/admin-panel/` (Bootstrap 5 + HTMX + DataTables + Chart.js — all served locally, no CDN)
* Captive-portal instructor login at `/portal/login/`, instructor dashboard at `/portal/`
* Django's own admin at `/django-admin/` (superusers only)

---

> **Installing on a client's PC?** Follow **[INSTALL.md](INSTALL.md)** (just the installation steps). For backups, updates and troubleshooting see **[docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)**.

## 1. Installing

**See [INSTALL.md](INSTALL.md)** - a step-by-step guide (about 12 short steps) for putting the system on a new Windows PC,
including the database, the router and the Windows services.

For developers on a PC that is already set up: `env\Scripts\python.exe manage.py runserver 0.0.0.0:8000`
(demo accounts: `manage.py seed_demo`).

### `.env`

| Key | Meaning |
|---|---|
| `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS` | Normal Django settings. Set a long random `SECRET_KEY` and `DEBUG=False` for real use. |
| `DATABASE_URL` | Written for you by `scripts\setup_database.ps1`. On the development PC it is PostgreSQL 17: `postgres://cdk_app:<password>@127.0.0.1:5433/cdk_portal` (app user `cdk_app`, database `cdk_portal`). `sqlite:///db.sqlite3` also works. |
| `MIKROTIK_HOST/PORT/USER/PASSWORD` | Router API access (`192.168.88.1`, `8728`, `djangoapi`, …). |
| `MIKROTIK_HOTSPOT_SERVER` | `hotspot1` |
| `MIKROTIK_WAN_INTERFACE` | `ether1` – used for the "bandwidth now" card (`/interface/monitor-traffic`). |
| `MIKROTIK_MOCK` | `True` = fake router (state in `.mock_router.json`) so everything runs without hardware. **`False` for the real router.** |
| `PORTAL_BASE_URL` | `http://192.168.88.5` (add `:8088` or another free port if port 80 is used by another app) – the services, the firewall rule, CSRF trust and the router's `login.html` redirect all follow it. |
| `SEED_ADMIN_PASSWORD` | Password given to the `admin` user by `seed_demo`. |

### Moving data from SQLite (only if you started on SQLite)

`DATABASE_URL=sqlite:///db.sqlite3 python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e sessions -e admin.logentry -o dump.json`,
point `.env` at PostgreSQL, `migrate`, then `loaddata dump.json`. The models use no database-specific features, so no code changes are needed.

## 2. Demo data (`python manage.py seed_demo`, safe to re-run)

| What | Value |
|---|---|
| School year / semester | `2026-2027` / `1st` (active) |
| Policies | `Standard` 2M/2M, 2048 MB (default) · `Restricted` 1M/1M, 500 MB |
| Admin | `admin` / `SEED_ADMIN_PASSWORD` |
| **Test instructor** | **`test` / `test123`**, `Standard` account |
| 10 sample instructors | `INS-0001` … `INS-0010` / `Instructor@123`, with session history for charts |

## 3. Running day to day (Windows)

Two terminals (or two Task Scheduler tasks, "run whether user is logged on or not", "at startup"):

| Task | Command |
|---|---|
| Web portal | `scripts\run_server.bat` (Waitress; port set at the top of the file, 80 by default to match `PORTAL_BASE_URL`) — or `python manage.py runserver 0.0.0.0:8000` in dev |
| Usage sync loop | `scripts\run_sync_sessions.bat` (`python manage.py sync_sessions`, every 30 s) |

## 3a. Deployed as Windows services (NSSM) - no terminal needed

Installed with `scripts\install_services.ps1` (run once from an **Administrator** PowerShell). Two services, both
start automatically at boot (before anyone logs in), restart within seconds if they crash, depend on PostgreSQL,
and log to `logs\` (rotated at 5 MB):

| Service | Runs | Log |
|---|---|---|
| `CDKPortal` | Waitress on the port from `PORTAL_BASE_URL` (80 unless the URL has a port) | `logs\CDKPortal.err.log` (Waitress writes its output here) |
| `CDKSync` | `manage.py sync_sessions --interval 30` | `logs\CDKSync.out.log` |

```powershell
Get-Service CDKPortal, CDKSync                 # status (also visible in services.msc)
powershell -ExecutionPolicy Bypass -File scripts\restart_services.ps1   # restart BOTH and verify the new code is live (admin)
powershell -ExecutionPolicy Bypass -File scripts\update.ps1      # after changing code: backup DB, pip, migrate, restart
powershell -ExecutionPolicy Bypass -File scripts\uninstall_services.ps1
```

**Updating a deployed copy never needs a rebuild:** edit/replace the files, then run `scripts\update.ps1` (it backs up
the database to `backups\`, installs packages, runs migrations and restarts both services; it stops before restarting
if the backup or a migration fails). A restart is always needed for code/template/static changes, and for `.env`
changes: use `scripts\restart_services.ps1`, which verifies the restart really happened (a bare `nssm restart` can be refused silently by Windows). Do not run `runserver` or `start_all.bat` while the services are installed (same port).

Deployment uses `DEBUG=False`. The PC must stay powered on, not sleep, and keep the static IP `192.168.88.5`.

Management commands:

| Command | Purpose |
|---|---|
| `python manage.py check_router` | Prints API connectivity, RouterOS version, hotspot server status. |
| `python manage.py sync_router` | One-shot: push all policies + active accounts, disable router users Django says are off. Run after router/DB downtime. |
| `python manage.py sync_sessions [--once] [--interval 30]` | Updates session bytes, adds usage to `SemesterAccount.total_bytes_used`, closes vanished sessions, kicks quota/semester/disabled violations, adopts sessions created by MAC-cookie auto-login. |
| `python manage.py make_login_html` | Regenerates `mikrotik/hotspot/login.html` from `PORTAL_BASE_URL`. |
| `python manage.py mock_traffic --mb 50` | Mock mode only: adds fake traffic to active sessions so you can watch monitoring/quota work. |

Router-side setup, the custom `login.html` upload, and the static-IP steps are in
**[docs/MIKROTIK_SETUP.md](docs/MIKROTIK_SETUP.md)**.

## 4. How it works

```
Phone ─► open SSID ─► router redirects to hotspot login.html
      ─► (our login.html) ─► http://192.168.88.5[:port]/portal/login/?mac=..&ip=..&dst=..
      ─► Django checks credentials + access rules
           user active · semester active · account active · quota not exceeded
      ─► via API: ensure profile (rate-limit=<up>M/<down>M, shared-users=1)
                  upsert hotspot user (random hotspot_password, limit-bytes-total = REMAINING quota,
                                       comment=cdk-managed, counters reset)
                  /ip/hotspot/active/login  (user, password, mac, ip)
      ─► Session row created ─► redirect to dst / dashboard
```

* The router password (`hotspot_password`) is random per semester account and never shown to instructors.
* Only router entries commented `cdk-managed` (plus the wizard's `test` user) are ever modified.
* `limit-bytes-total` is the **remaining** quota and counters are reset at each login, so the router can cut a
  instructor exactly at the quota; `sync_sessions` then reads the user's counters to record the final bytes and the
  reason (`quota`).
* Kicks also remove the user's MAC cookies so a kicked/disabled instructor cannot silently re-authenticate.
* If the router is unreachable, pages show a friendly message; admin actions still save in Django and are
  reconciled by `sync_router` / `sync_sessions` when it returns.

### Who can do what (roles)

| Role | Wi-Fi portal | Admin panel (instructors, policies, semesters, monitoring, reports, logs) | Staff accounts page | Django admin (`/django-admin/`) |
|---|---|---|---|---|
| **Instructor** | yes | no | no | no |
| **Staff** | no | **yes** | no | no |
| **Superuser** | no | **yes** | **yes** (adds/edits staff and superusers) | **yes** |

The first account made with `manage.py create_admin` is a superuser. Superusers add staff (or more superusers) under **Staff accounts**.
The system never lets you remove your own superuser role, or deactivate or demote the last active superuser.

### Admin panel map

| Area | URL |
|---|---|
| Dashboard (live cards, 7-day chart, recent logs) | `/admin-panel/` |
| Instructors — list/filters/bulk, add/edit/view, reset password, policy, masterlist import | `/admin-panel/instructors/` |
| Staff accounts (superusers only; you cannot deactivate yourself or the last superuser) | `/admin-panel/admins/` |
| School years & semesters ("Set active" confirmation modal) | `/admin-panel/semesters/` |
| Bandwidth policies + "Sync to router" status | `/admin-panel/policies/` |
| Live monitoring (HTMX every 5 s, Kick) | `/admin-panel/monitoring/` |
| Reports (per instructor/day, top consumers, CSV + PDF) | `/admin-panel/reports/` |
| System logs (audit trail, filters) | `/admin-panel/logs/` |

**Masterlist import** (CSV or XLSX): upload → preview with per-row validation → confirm. Columns:
`username, first_name, middle_name, last_name, extension_name`
(template download on the upload page). New instructors get the `username` from the file and a generated password,
delivered in a downloadable credentials sheet that is kept for 10 minutes in the admin's session and is not
stored anywhere else; instructors must change it at first login. Re-uploading updates the listed instructors (name) and
creates their account for the active semester if missing, without touching existing passwords.

**Self-registration with approval.** The Wi-Fi login page has a **Create an account** link (`/portal/signup/`).
An instructor enters a username, their name and a password of their own; the same username is what they sign in with. The account is created as
**pending**: they can sign in to see their status, but they cannot get online until an administrator approves it.
Administrators see a count badge on **Instructors** in the sidebar and a banner on the dashboard, can filter the list
by *Registration* (pending / approved / rejected), and can **Approve** (choosing a bandwidth policy; an internet
account is created for the active semester) or **Reject** from the list, the instructor's page, or in bulk. Rejected
registrations can still be approved later. Instructors added by an administrator or imported from a masterlist are
approved immediately. Sign-ups are limited to 5 per device address per hour, and every registration, approval and
rejection is recorded in the audit log.

**No e-mail addresses** are collected or stored anywhere (no field on accounts, forms or the masterlist).

## 5. Tests

```powershell
python manage.py test           # 160+ tests, ~5 s, no router needed
```

Covers access rules, the single-active-semester rule, masterlist validation/import, quota enforcement against the
mock router, captive-portal login (reasons, rate limiting, RBAC), admin actions, reports/exports, and the real
`MikroTik` class against an in-process fake RouterOS API server (`network/test_real_router.py`).

> **Not covered by automated tests:** behaviour on the physical hEX router (e.g. exact RouterOS error texts,
> `reset-counters`/`limit-bytes-total` semantics on 7.20.8). Follow the "Verify, in this order" checklist in
> `docs/MIKROTIK_SETUP.md` and run `check_router` first.

## 6. Security notes

* RBAC via `admin_required` / `instructor_required`; instructors cannot reach admin pages or other instructors' data.
* CSRF on every form; login rate limit: 5 failures per (username, IP) → 5-minute lockout (staff and portal logins).
* Every admin action and every login success/failure/denial is written to the audit log.
* Router credentials only in `.env` (git-ignored). `.env.example` is committed.
* The captive portal runs over plain HTTP on the LAN (the router redirect is HTTP too). Do not reuse an instructor's
  portal password elsewhere; consider HTTPS termination if you later put the portal behind a reverse proxy.
* Imported passwords use a faster PBKDF2 setting (20k iterations, ~9 ms each: a 2,000-instructor import takes ~20 s instead of ~14 min) because they are throwaway temporary passwords;
  Django upgrades each hash to the full strength automatically at that instructor's first login.

## 7. Project layout

```
config/       settings, urls, test helpers
accounts/     custom User, AuditLog, login/lockout, RBAC decorators
academics/    SchoolYear, Semester (one active), BandwidthPolicy
instructors/     Instructor, SemesterAccount, masterlist import
network/      Session, mikrotik.py (real + mock router), services.py (rules), sync.py, management commands
adminpanel/   admin UI (views, forms)       portal/  instructor UI + captive-portal login
mikrotik/hotspot/login.html   router redirect page       docs/MIKROTIK_SETUP.md   router-side steps
```
