# Deployment guide - installing the CDK Wi-Fi portal on a new Windows PC

> **Only want the installation steps?** Use the short guide: **[INSTALL.md](../INSTALL.md)**. This longer document adds the reasons behind each step, backups, updates and troubleshooting.

Use this when you hand the system to a client (or move it to a new server). Follow the steps in order.
Everything marked **verified** was actually tested on a clean folder with a brand-new virtual environment
(install from `requirements.txt`, migrate, create admin, 173 automated tests passing).

> **Time needed:** about 45–60 minutes for the server, plus the router work (see step 8).
> **Who:** someone with Administrator rights on the PC and WinBox access to the MikroTik router.

---

## 0. What you are installing

```
 Phones ──Wi-Fi──► Access point ──► MikroTik hEX (hotspot) ◄──API (8728)── Windows PC ("the server")
                                          │                                   ├─ CDKPortal  (web, port 80)
                                          └── redirects logins to ───────────►├─ CDKSync    (usage/quota loop)
                                                                              └─ PostgreSQL (database)
```

The Windows PC is the "server". It must stay on, and always have the same IP address (default `192.168.88.5`).

---

## 1. Shopping list - everything that must be installed

| # | Software | Version | Notes |
|---|---|---|---|
| 1 | **Windows 10 / 11, 64-bit** | any current | You need an **Administrator** account. |
| 2 | **Python** | 3.10 or newer (**3.13 tested**) | python.org installer. Tick **"Add python.exe to PATH"** and choose **"Install for all users"** (Customize installation → Advanced → *Install Python for all users*). Needed because the services run as a system account. |
| 3 | **PostgreSQL** | 15 or newer (**17 tested**) | EnterpriseDB installer. Installs the server and `psql`. Keep the defaults (port **5432**). **Write down the `postgres` superuser password you choose.** pgAdmin is optional. |
| 4 | *(downloaded automatically)* **NSSM 2.24** | – | `scripts\install_services.ps1` downloads it and verifies its checksum. Needs internet once. |

That's all (**Git for Windows** is optional: it makes installing and updating from GitHub one command; without it use "Download ZIP"). **Not needed:** Docker, Node.js, Visual Studio, any web server, any CDN or internet access for instructors to see the login page (all Bootstrap/HTMX/Chart.js files ship inside the project).

Internet is needed on the server **only during installation** (Python packages, NSSM). To install offline, run `pip download -r requirements.txt -d wheels` on a connected PC, copy the `wheels` folder over, and use `pip install --no-index --find-links wheels -r requirements.txt`.

---

## 2. Prepare the PC

1. **Static IP** – Settings → Network & Internet → Ethernet → *Edit* IP assignment → Manual:

   | Field | Value |
   |---|---|
   | IP address | `192.168.88.5` |
   | Prefix length | `24` (255.255.255.0) |
   | Gateway | `192.168.88.1` |
   | DNS | `192.168.88.1` |

   `192.168.88.5` is outside the router's DHCP pool (`.10–.254`). If the client's network uses a different range, pick an address outside *their* pool and use it everywhere this guide says `192.168.88.5`.
2. **Never sleep** – Settings → System → Power: *Screen* may turn off, but *Sleep* = **Never**. Device Manager → network adapter → Properties → Power Management → untick "Allow the computer to turn off this device".
3. **Network profile** – make the Ethernet network **Private** (Settings → Network → Ethernet → Properties).
4. **Clock** – Settings → Time & language → set the time zone (Philippines = UTC+8) and **Sync now**. Session times and reports depend on it.
5. **Windows Update** – set *Active hours* to the school hours so the PC doesn't reboot during the day. (The services start by themselves after any reboot.)

---

## 3. Put the code on the PC - **use a SHORT path**

> **Important (verified):** install into a short folder such as **`C:\CDKPortal`**. In a deeply nested folder
> (for example inside `Desktop\...\...`) Windows' 260-character path limit breaks `pip install`
> (`psycopg_binary...dll: No such file`).

**Option A - from GitHub (recommended once the repository is set up).** The repository is
<https://github.com/0jieE/mikrotik-captive-portal>. It contains no passwords: `.env`, the database, backups and logs are never in it.

```powershell
# with Git for Windows installed on the client PC:
cd C:\
git clone https://github.com/0jieE/mikrotik-captive-portal.git CDKPortal
cd C:\CDKPortal
```

No Git on the client PC? Open the repository page in a browser → **Code → Download ZIP**, then:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\mikrotik-captive-portal-main.zip" -DestinationPath C:\
Rename-Item C:\mikrotik-captive-portal-main CDKPortal
cd C:\CDKPortal
```

If the repository is **private**, the client PC must be signed in to a GitHub account that can see it
(`git clone` asks you to log in in the browser; for "Download ZIP" sign in first).

**Option B - from a zip built on your PC.** Build a clean package (it leaves out secrets, the database and the virtual environment):

```powershell
cd <your project folder>
powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 -Version 1.0
# -> release\cdk-wifi-portal-1.0.zip
```

Copy that zip to the client PC (USB/Drive), then:

```powershell
Expand-Archive cdk-wifi-portal-1.0.zip -DestinationPath C:\
Rename-Item C:\cdk-wifi-portal-1.0 CDKPortal
cd C:\CDKPortal
```

(Never copy your own `env\`, `.env` or `db.sqlite3` to the client — the virtual environment contains hard-coded paths and will not work on another PC, and `.env` holds your passwords.)

---

## 4. Create the Python environment

Open **PowerShell** (normal is fine) in `C:\CDKPortal`:

```powershell
python --version                       # must say 3.10 or newer
python -m venv env
env\Scripts\python.exe -m pip install -r requirements.txt     # ~1 minute
env\Scripts\python.exe -c "import django, librouteros, psycopg; print('OK', django.__version__)"
```

---

## 5. Create the database (PostgreSQL) - one script

PostgreSQL must already be installed (step 1) and you must know the password of its **`postgres`** user. From `C:\CDKPortal`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_database.ps1
```

It asks for that `postgres` password, then creates the database user `cdk_app` (random password, never shown) and the database
`cdk_portal`, tests the new login, and writes `DATABASE_URL` (and a fresh `SECRET_KEY`) into `.env` - creating `.env` from
`.env.example` if it does not exist yet. It prints `DONE` when finished, or `STOPPED:` followed by what to fix. Safe to run again.

Typical stops: *PostgreSQL is not installed* (install it), *Could not log in as 'postgres'* (wrong password - or forgotten: run
`scripts\reset_postgres_password.ps1` from an Administrator PowerShell and try again), *does not answer on port 5432/5433/5434*
(start the `postgresql-x64-NN` service, or pass `-Port <number>`).

> **Change a weak `postgres` password.** If the superuser is still on a default-style password, set a strong one:
> `psql -U postgres` then `ALTER USER postgres WITH PASSWORD 'a-strong-password';`. The portal never uses the `postgres` user.

*Prefer typing the commands yourself?* See "Doing it by hand instead" in the README's database section.

---

## 6. Configure `.env`

Step 5 already created `.env` and filled in `DATABASE_URL` and `SECRET_KEY`. Open it and set the rest
(if the file does not exist, step 5 was skipped - run `scripts\setup_database.ps1` first):

```powershell
notepad .env
```

| Setting | What to put |
|---|---|
| `DEBUG` | `False` |
| `SECRET_KEY` | Already generated by step 5. (To make another: `env\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(50))"`) |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,192.168.88.5` (add the hotspot DNS name if you use one, e.g. `login.cdk.local`) |
| `DATABASE_URL` | Already written by step 5 - leave it as is. |
| `MIKROTIK_HOST` | `192.168.88.1` (the router's LAN IP) |
| `MIKROTIK_USER` / `MIKROTIK_PASSWORD` | The `djangoapi` user you create on the router in step 8 |
| `MIKROTIK_HOTSPOT_SERVER` | `hotspot1` (the hotspot server's name on the router) |
| `MIKROTIK_WAN_INTERFACE` | `ether1` (the internet-facing port) |
| `MIKROTIK_MOCK` | **`False`** (`True` = fake router for demos only) |
| `PORTAL_BASE_URL` | `http://192.168.88.5` (this PC's static IP). **If something else on this PC already uses port 80** (another web app, Docker, IIS ...) add a free port, e.g. `http://192.168.88.5:8088`. The services, the firewall rule and the router redirect all follow this value (see "Changing the portal port" below). |

Keep `.env` private. It is the only place passwords live.

---

## 7. Create the tables and the first administrator

```powershell
cd C:\CDKPortal
env\Scripts\python.exe manage.py migrate
env\Scripts\python.exe manage.py create_admin --username admin
```

`create_admin` asks for a password (min. 8 characters). **Use this command, not `createsuperuser`** — only `create_admin` sets the flag that lets an account into `/admin-panel/`.
Running it again for the same username resets that admin's password (handy if the client forgets it).

> **Do not run `seed_demo` for a client.** It creates demo instructors and the `test` / `test123` login.

Quick self-test (optional): `env\Scripts\python.exe manage.py test` → *OK* (about 10 s; needs `ALTER ROLE cdk_app CREATEDB` on PostgreSQL, or run it with `DATABASE_URL=sqlite:///db.sqlite3` set in the shell).

---

## 8. Router (MikroTik) side

All router commands and screenshots-worth of detail are in **[MIKROTIK_SETUP.md](MIKROTIK_SETUP.md)**. In short, in WinBox → New Terminal:

1. LAN `192.168.88.1/24` on `bridge`, DHCP pool `192.168.88.10–254`, WAN on `ether1` (DHCP client) — and the **Hotspot** set up on `bridge` (`/ip hotspot setup`, server name `hotspot1`, profile `hsprof1`). *If the client's router is brand new, this is the biggest job; do it first and confirm a phone gets the hotspot login page before continuing.*
2. API user for the portal, API allowed only from the server:
   ```routeros
   /user group add name=djangoapi policy=api,read,write,test,!local,!telnet,!ssh,!ftp,!reboot,!winbox,!web,!sniff,!sensitive,!romon
   /user add name=djangoapi group=djangoapi password=<strong-password>
   /ip service set api disabled=no address=192.168.88.5/32
   ```
3. Bypass the server and the access point, and let phones reach the portal before logging in:
   ```routeros
   /ip hotspot ip-binding add address=192.168.88.5 type=bypassed comment="Django server"
   /ip hotspot ip-binding add address=192.168.88.2 type=bypassed comment="Access point"
   /ip hotspot walled-garden ip add dst-address=192.168.88.5 action=accept comment="Django portal"
   /ip hotspot profile set hsprof1 login-by=http-pap,http-chap,mac-cookie
   ```
   **And disable FastTrack, otherwise speed limits are silently ignored** (MikroTik's default firewall enables it):
   ```routeros
   /ip firewall filter disable [find action=fasttrack-connection]
   ```
   `check_router` warns while it is on, and the admin panel shows a red banner. Details: [MIKROTIK_SETUP.md](MIKROTIK_SETUP.md) section 5b.
4. Generate and upload the redirect page. On the server:
   ```powershell
   env\Scripts\python.exe manage.py make_login_html     # reads PORTAL_BASE_URL from .env
   ```
   Then WinBox → Files → `hotspot` folder → **back up the original `login.html` first**, then drag `C:\CDKPortal\mikrotik\hotspot\login.html` in.
5. Put the same `djangoapi` password in `.env` (`MIKROTIK_PASSWORD`).
6. Test the link from the server:
   ```powershell
   ping 192.168.88.1
   env\Scripts\python.exe manage.py check_router     # expect: API connection: OK ... Hotspot server 'hotspot1': running
   ```
   *If `ping` says "Destination net unreachable": the router's hotspot is treating the PC as an unauthenticated phone. The server's IP is not bypassed yet (step 3) or the PC isn't on `192.168.88.5`.*

---

## 9. Install as Windows services (starts at boot, no terminal)

Open **PowerShell as Administrator** (right-click → Run as administrator):

```powershell
cd C:\CDKPortal
powershell -ExecutionPolicy Bypass -File scripts\install_services.ps1
```

It will: download NSSM (checksum-verified), install **CDKPortal** (web) and **CDKSync** (usage loop), set both to start automatically after PostgreSQL, restart on crash, open the firewall for the portal port, start them, and check the login page. Expected last lines:

```
CDKPortal Running Automatic
CDKSync   Running Automatic
HTTP check: portal login page -> 200
```

Then confirm it survives a reboot (restart the PC once and, without logging in or opening anything, browse to `http://192.168.88.5/` from another device).

Logs: `C:\CDKPortal\logs\` (`CDKPortal.err.log`, `CDKSync.out.log`). Status: `Get-Service CDKPortal, CDKSync`.

---

## 10. First-time setup in the admin panel

Open `http://192.168.88.5/accounts/login/` and sign in with the admin from step 7. In this order:

1. **Bandwidth policies** → add at least one (e.g. *Standard*: 2 Mbps down/up, 2048 MB quota) and tick **default**. Click **Sync all to router**.
2. **Semesters** → *Add school year* (`2026-2027`) → *Add semester* → **Set active** (only one semester can be active; instructors can't connect without it).
3. **Instructors** → **Upload masterlist** (CSV or Excel; download the template on that page) → preview → confirm. Download the **credentials sheet** immediately (it's kept 10 minutes) and hand the passwords out; instructors must change them at first login.
4. **Self-registration (optional to explain to the client):** instructors can also create their own account from the Wi-Fi login page (**Create an account**). Those registrations stay *pending* until an administrator approves them under **Instructors** (a badge in the sidebar and a banner on the dashboard show how many are waiting). Approving gives them an internet account on the chosen policy for the active semester.
5. **Live monitoring** → should show *Router: Online* and no "Usage sync is not running" banner.
6. **Phone test:** connect a phone to the Wi-Fi → the portal login appears → sign in with an instructor → it's online; check *Live monitoring*, then use the Kick button.

---

## 11. Handover checklist (for the client)

Give the client, in writing (store them safely, e.g. in a sealed envelope or the school's password manager):

- [ ] Admin panel address `http://192.168.88.5/accounts/login/` and the **admin username/password**
- [ ] The **PostgreSQL `postgres`** password and the **`cdk_app`** password
- [ ] The **router** WinBox admin login and the **`djangoapi`** password
- [ ] A copy of `C:\CDKPortal\.env` (kept off the server, e.g. on a USB drive in a safe)
- [ ] A short list of who to call (you) and the version of the release (`cdk-wifi-portal-1.0.zip`)
- [ ] Show them: where the logs are, how to see service status, how to kick an instructor, how to reset an instructor password, how to switch semesters, how to export a report

Teach them these **two rules**: the server must stay powered on and on the network, and when the semester changes they use **Semesters → Set active** (and upload the new masterlist).

---

## 12. Operating it

### Backups (do this on day one)

A manual backup:

```powershell
powershell -ExecutionPolicy Bypass -File C:\CDKPortal\scripts\backup_db.ps1 -Keep 14
```

Schedule it **nightly at 02:00** (Administrator PowerShell, once):

```powershell
schtasks /create /tn "CDK DB Backup" /sc daily /st 02:00 /ru SYSTEM /rl HIGHEST /tr "powershell -NoProfile -ExecutionPolicy Bypass -File C:\CDKPortal\scripts\backup_db.ps1 -Keep 14"
```

Backups go to `C:\CDKPortal\backups\` (newest 14 kept). **Copy that folder to another disk or USB regularly** — a backup on the same PC doesn't survive a dead disk.

Restore (stop the services first):

```powershell
tools\nssm.exe stop CDKSync ; tools\nssm.exe stop CDKPortal
$env:PGPASSWORD = "<the cdk_app password: the part after cdk_app: in DATABASE_URL in .env>"
& "C:\Program Files\PostgreSQL\17\bin\pg_restore.exe" -U cdk_app -h 127.0.0.1 -p 5432 -d cdk_portal --clean --if-exists --no-owner backups\db-YYYYMMDD-HHMMSS.dump
$env:PGPASSWORD = $null
tools\nssm.exe start CDKPortal ; tools\nssm.exe start CDKSync
```

### Updating to a new version (no rebuild)

**If it was installed with `git clone` (Option A):** just pull, then run the update script. `git pull` never touches `.env`,
`env\`, `logs\`, `backups\` or `tools\` because none of them are in the repository.

```powershell
cd C:\CDKPortal
git pull
powershell -ExecutionPolicy Bypass -File scripts\update.ps1     # as Administrator
```

**If it was installed from a zip:**

1. Build a new zip (`make_release.ps1 -Version 1.1`) and copy it to the server.
2. Copy the new files over the installed ones **without touching your settings, data or environment** (Administrator PowerShell):
   ```powershell
   Expand-Archive C:\Users\<you>\Downloads\cdk-wifi-portal-1.1.zip -DestinationPath C:\_cdk_new -Force
   robocopy C:\_cdk_new\cdk-wifi-portal-1.1 C:\CDKPortal /E /XD env logs backups tools /XF .env
   ```
   (`robocopy` without `/MIR` only adds and overwrites files, so `.env`, `env\`, `logs\`, `backups\` and `tools\` are never touched. Exit codes 0–7 mean success. Afterwards delete `C:\_cdk_new`.)
3. Administrator PowerShell: `powershell -ExecutionPolicy Bypass -File C:\CDKPortal\scripts\update.ps1`
   → backs up the database, installs packages, runs migrations, restarts both services. If the backup or migration fails, it stops and the old version keeps running.
   Check `.env.example` of the new version for any **new settings** and add them to the client's `.env` before step 3 (release notes should mention them).

### Everyday commands

| Task | Command |
|---|---|
| Status | `Get-Service CDKPortal, CDKSync` |
| Restart (after editing `.env` or code) | `powershell -ExecutionPolicy Bypass -File C:\CDKPortal\scripts\restart_services.ps1` (Administrator). It stops both services, clears any leftover worker, starts them and **verifies** the processes are new and the page answers. It prints `RESTART OK` when done. |
| Web errors | `Get-Content C:\CDKPortal\logs\CDKPortal.err.log -Tail 50` |
| Sync output | `Get-Content C:\CDKPortal\logs\CDKSync.out.log -Tail 20` |
| Router check | `env\Scripts\python.exe manage.py check_router` |
| Push everything to the router | `env\Scripts\python.exe manage.py sync_router` |
| Reset an admin password | `env\Scripts\python.exe manage.py create_admin --username admin` |
| Remove the services | `powershell -ExecutionPolicy Bypass -File scripts\uninstall_services.ps1` (data is kept) |

### Moving to another PC later

Run `backup_db.ps1` on the old PC, do steps 1–9 on the new PC **except** creating admin accounts, restore the dump (see "Restore"), and copy the old `.env` (change `PORTAL_BASE_URL` / IPs only if they changed). Give the new PC the same static IP, or update the router rules (step 8) and regenerate/upload `login.html`.

### Changing the portal port (when port 80 is taken by another app)

1. Choose a free port (check with `Get-NetTCPConnection -State Listen -LocalPort 8088`) and set it in `.env`:
   `PORTAL_BASE_URL=http://192.168.88.5:8088`
2. Re-run `scripts\install_services.ps1` as Administrator. It moves `CDKPortal` to the new port, adds the firewall rule for it and **removes the old rule**.
3. **Update the router**, because phones are sent to the portal by the router's `hotspot/login.html`, and that URL must carry the same port.
   Run `manage.py make_login_html` and upload the new `mikrotik\hotspot\login.html`, or just add `:8088` to the URL inside the existing file.
   The walled-garden entry and the bypass rule are by IP, so they already cover every port.
4. Check from a phone: connect to the Wi-Fi, the login page must appear. Addresses you type by hand now need the port, e.g. `http://192.168.88.5:8088/accounts/login/`.

---

## 13. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `pip install` fails with *No such file or directory ...dll* | Folder path too long. Install in `C:\CDKPortal`. |
| `check_router` → *did not answer in time* | Wrong `MIKROTIK_HOST`, server not on `192.168.88.5`, or router API restricted to another address. |
| `ping 192.168.88.1` → *Destination net unreachable* | Server IP not bypassed in the hotspot (step 8.3) or wrong IP on the PC. |
| `check_router` → *rejected the API username or password* | `.env` `MIKROTIK_USER/PASSWORD` don't match the `djangoapi` user. |
| Instructor login says *network controller is not responding* | The portal can't reach the router API. Run `check_router`; see `login.router_error` in **System logs** for the exact router message. |
| Data usage / quota doesn't change | **CDKSync** not running. The admin dashboard shows an amber "Usage sync is not running" banner. `Get-Service CDKSync`, then `Start-Service CDKSync`. |
| Page can't be reached from a phone | Firewall rule missing (re-run `install_services.ps1`), wrong network profile (should be Private), or walled-garden entry missing. |
| `DisallowedHost` / Bad Request (400) | The address you typed isn't in `ALLOWED_HOSTS`. Add it, then restart CDKPortal. |
| Changes don't show up / odd 500 errors after an update | An old worker is still running. Run `scripts\restart_services.ps1` and check it ends with `RESTART OK`. (Don't rely on a bare `nssm restart`: Windows refuses it silently while another service depends on the one being restarted.) |
| Service won't start | Read `logs\CDKPortal.err.log`. Common: wrong `DATABASE_URL`, PostgreSQL stopped, or the port is already used by another program (IIS/Apache/Docker/another web app). Pick a free port - see "Changing the portal port" below. |
| Admin can't enter the panel | The account was made with `createsuperuser`. Run `manage.py create_admin --username <name>`. |
| Everyone suddenly "no active semester" | No semester is active. **Semesters → Set active.** |

---

## 14. Security notes for the client

- The portal runs over plain HTTP inside the school LAN (the router's captive-portal redirect is HTTP). Don't expose the server to the internet; no port-forwarding.
- Admin passwords: long and unique. Instructors' temporary passwords force a change at first login.
- Keep Windows updated, and keep `.env` and `backups\` readable only by administrators.
- The router API (port 8728) is restricted to the server's IP; keep that rule.
- Every admin action and login attempt is recorded under **System logs**.
