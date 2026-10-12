# Installation guide

How to install the CDK Wi-Fi portal on a **new Windows PC**, from nothing to a working system.
Follow the steps **in order**. Every step says what you should see when it worked.

**You need:** a Windows 10/11 PC (the "server"), an Administrator login on it, internet on that PC for about 15 minutes,
and a MikroTik router that already runs the hotspot (see "Before you start").

Where you see a gray box, copy the text into **PowerShell** and press Enter.
(Start menu → type `PowerShell`. For steps marked **Administrator**: right-click it → *Run as administrator*.)

---

## Before you start

- The router must already have the **hotspot** working: server name `hotspot1`, profile `hsprof1`, LAN `192.168.88.1`.
  A phone that connects to the Wi-Fi should already see the router's login page. If the router is brand new, set up the
  hotspot first (see [docs/MIKROTIK_SETUP.md](docs/MIKROTIK_SETUP.md)).
- Decide the server's fixed address. This guide uses **`192.168.88.5`**. If the client's network is different, use an unused
  address outside the router's DHCP range, and use it everywhere below instead of `192.168.88.5`.

---

## Step 1 - Install two programs

1. **Python 3.13** - https://www.python.org/downloads/
   - In the installer tick **"Add python.exe to PATH"**.
   - Choose **Customize installation → Next → tick "Install Python for all users"**.
2. **PostgreSQL 17** - https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
   - Click Next through the installer and keep the defaults (port `5432`).
   - When it asks for a password for the **`postgres`** user, choose one and **write it down**. You need it in Step 5.

Check (open a new PowerShell):
```powershell
python --version
```
You should see `Python 3.13.x` (any 3.10 or newer works).

---

## Step 2 - Give the server a fixed address

Settings → Network & Internet → **Ethernet** → **Edit** next to *IP assignment* → **Manual** → turn on IPv4:

| Field | Type this |
|---|---|
| IP address | `192.168.88.5` |
| Subnet prefix length | `24` |
| Gateway | `192.168.88.1` |
| Preferred DNS | `192.168.88.1` |

Also: Settings → System → Power → set **Sleep** to **Never** (the server must stay on).

Check:
```powershell
ping 192.168.88.1
```
You should see replies from `192.168.88.1`.

---

## Step 3 - Put the code on the PC

Use a **short folder name** - `C:\CDKPortal`. (Long folder paths break the installation.)

**If Git is installed:**
```powershell
cd C:\
git clone https://github.com/0jieE/mikrotik-captive-portal.git CDKPortal
```

**If Git is not installed:** open https://github.com/0jieE/mikrotik-captive-portal in a browser → green **Code** button →
**Download ZIP**, then:
```powershell
Expand-Archive "$env:USERPROFILE\Downloads\mikrotik-captive-portal-main.zip" -DestinationPath C:\
Rename-Item C:\mikrotik-captive-portal-main CDKPortal
```

From now on every command is run **inside `C:\CDKPortal`**:
```powershell
cd C:\CDKPortal
```
Check: `dir` shows `manage.py`, `README.md`, `scripts` ...

---

## Step 4 - Install the Python packages

```powershell
cd C:\CDKPortal
python -m venv env
env\Scripts\python.exe -m pip install -r requirements.txt
```
This takes about a minute. Check:
```powershell
env\Scripts\python.exe -c "import django, librouteros, psycopg; print('OK')"
```
You should see `OK`.

---

## Step 5 - Create the database

```powershell
cd C:\CDKPortal
powershell -ExecutionPolicy Bypass -File scripts\setup_database.ps1
```
It asks for the **`postgres` password** from Step 1. Type it (the letters do not show) and press Enter.
You should see five steps `[1/5]` to `[5/5]` and then **`DONE`**. The script created the database and a file called `.env`
with the database settings in it.

If it says **`STOPPED:`**, read the reason:

| It says | Do this |
|---|---|
| PostgreSQL is not installed | Do Step 1 again. |
| Could not log in as 'postgres' | The password is wrong. Run the script again and type it carefully. If it is forgotten: open PowerShell as **Administrator** and run `scripts\reset_postgres_password.ps1`, then this step again. |
| does not answer on port 5432 ... | Start PostgreSQL: Windows key → type `Services` → find `postgresql-x64-17` → right-click → Start. Then run this step again. |

---

## Step 6 - Prepare the router

Do this in **WinBox → New Terminal** on the MikroTik. **Change `PICK-A-PASSWORD`** to a password of your own and write it
down - you need it in Step 7.

```routeros
/user group add name=djangoapi policy=api,read,write,test,!local,!telnet,!ssh,!ftp,!reboot,!winbox,!web,!sniff,!sensitive,!romon
/user add name=djangoapi group=djangoapi password=PICK-A-PASSWORD
/ip service set api disabled=no address=192.168.88.5/32
/ip hotspot ip-binding add address=192.168.88.5 type=bypassed comment="Django server"
/ip hotspot walled-garden ip add dst-address=192.168.88.5 action=accept comment="Django portal"
/ip hotspot profile set hsprof1 login-by=http-pap,http-chap,mac-cookie
/ip firewall filter disable [find action=fasttrack-connection]
```

What these do: create a limited user for the portal, let only the server use the router's API, let the server and phones reach
the portal, and turn off *FastTrack* (otherwise the speed limits do not work).

If the Wi-Fi access point has its own address (for example `192.168.88.2`), also run:
```routeros
/ip hotspot ip-binding add address=192.168.88.2 type=bypassed comment="Access point"
```

---

## Step 7 - Fill in the settings file

```powershell
notepad C:\CDKPortal\.env
```
Change **only** these lines (leave the rest as it is). Save and close.

| Line | Set it to |
|---|---|
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,192.168.88.5` |
| `MIKROTIK_HOST` | `192.168.88.1` |
| `MIKROTIK_USER` | `djangoapi` |
| `MIKROTIK_PASSWORD` | the password you chose in Step 6 |
| `MIKROTIK_MOCK` | `False` |
| `PORTAL_BASE_URL` | `http://192.168.88.5` |

If another program on this PC already uses port 80 (a web server, Docker, ...), use a free port instead, for example
`PORTAL_BASE_URL=http://192.168.88.5:8088`. Then use `:8088` after the address wherever you open the portal below.

(`DATABASE_URL` and `SECRET_KEY` were filled in by Step 5. Do not change them.)

---

## Step 8 - Create the tables and the first administrator

```powershell
cd C:\CDKPortal
env\Scripts\python.exe manage.py migrate
env\Scripts\python.exe manage.py create_admin --username admin
```
The first command prints a list of lines ending with `OK`. The second asks you to **type a password twice** (at least 8
characters). Write it down.

This first account is a **superuser**: it can use the admin panel **and** the Django admin. Staff accounts (admin panel only)
are added later by a superuser in the admin panel, under **Staff accounts**.

---

## Step 9 - Check the connection to the router

```powershell
cd C:\CDKPortal
env\Scripts\python.exe manage.py check_router
```
You should see `API connection: OK` and `Hotspot server 'hotspot1': running`.

| It says | Cause |
|---|---|
| did not answer in time | Wrong `MIKROTIK_HOST`, or the PC is not on `192.168.88.5` (Step 2), or Step 6 was not done. |
| rejected the API username or password | `MIKROTIK_PASSWORD` in `.env` is not the one from Step 6. |

---

## Step 10 - Send phones to the portal (login page)

Create the redirect page, then copy it to the router.

```powershell
cd C:\CDKPortal
env\Scripts\python.exe manage.py make_login_html
```
It creates `C:\CDKPortal\mikrotik\hotspot\login.html`.

In **WinBox → Files → `hotspot` folder**:
1. Right-click the existing `login.html` → copy/download it to your PC as a **backup**.
2. Drag `C:\CDKPortal\mikrotik\hotspot\login.html` from the PC into the `hotspot` folder (answer *yes* to overwrite).

---

## Step 11 - Install the portal as a Windows service (Administrator)

This makes the portal start by itself whenever the PC starts. Nobody has to open a terminal.

Open PowerShell **as Administrator**, then:
```powershell
cd C:\CDKPortal
powershell -ExecutionPolicy Bypass -File scripts\install_services.ps1
```
Wait about a minute. The last lines should be:
```
CDKPortal Running Automatic
CDKSync   Running Automatic
HTTP check: portal login page -> 200
```

---

## Step 12 - Final test

1. On the server, open a browser: **http://192.168.88.5/accounts/login/** (add `:8088` if you used another port in Step 7).
   Sign in with `admin` and the password from Step 8. You should see the admin dashboard.
2. Restart the PC once. Without signing in to Windows or opening anything, open
   **http://192.168.88.5/** from your phone while on the Wi-Fi. The portal page should appear by itself.

**The installation is finished.**
To start using it (add a bandwidth policy, a semester and instructors) and for backups, updates and problems, see
[docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) and the [README](README.md).
