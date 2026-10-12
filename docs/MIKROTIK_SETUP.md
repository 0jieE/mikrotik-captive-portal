# MikroTik & Windows setup for the CDK portal

> **The portal never runs these commands for you.** Paste them yourself in WinBox → *New Terminal*
> (or SSH) on the hEX refresh E50UG (RouterOS 7.20.8). Do them in this order and test after each part.

Assumptions (already working on your network): LAN `192.168.88.1/24` on `bridge`, hotspot server
`hotspot1` with profile `hsprof1`, access point `192.168.88.2` in bridge mode, Django server `192.168.88.5`.

---

## 1. API user for Django (least privilege)

```routeros
/user group add name=djangoapi policy=api,read,write,test,!local,!telnet,!ssh,!ftp,!reboot,!winbox,!web,!sniff,!sensitive,!romon
/user add name=djangoapi group=djangoapi password=<strong-password>
```

Put the same user/password in the portal's `.env` (`MIKROTIK_USER`, `MIKROTIK_PASSWORD`). The `.env` file is
git-ignored; only `.env.example` is committed.

## 2. Allow the API only from the Django server

```routeros
/ip service set api disabled=no address=192.168.88.5/32
```

Plain API (port 8728) is what the portal uses. Because it is unencrypted, this restriction to a single
LAN address matters: keep it. (While developing from a different PC, temporarily widen the address, then restore it.)
Leave other services (telnet/ftp/www) disabled unless you need them.

## 3. Keep the server and the access point out of the hotspot

```routeros
/ip hotspot ip-binding add address=192.168.88.5 type=bypassed comment="Django server"
/ip hotspot ip-binding add address=192.168.88.2 type=bypassed comment="Access point"
```

## 4. Let unauthenticated phones reach the portal (walled garden)

Instructors are not logged in yet when they open the login page, so the router must let them reach Django:

```routeros
/ip hotspot walled-garden ip add dst-address=192.168.88.5 action=accept comment="Django portal"
```

All portal assets (Bootstrap, scripts, fonts) are served by Django itself from `/static/`, so no
internet access is needed to render the login page.

## 5. Login methods

```routeros
/ip hotspot profile set hsprof1 login-by=http-pap,http-chap,mac-cookie
```

**About `mac-cookie`:** after a successful login the router remembers the device's MAC and may log it in
again automatically. Django handles this: when it kicks an instructor (admin kick, quota, logout) it also deletes
that user's cookies on the router; and `sync_sessions` adopts or kicks any session it did not create, so
usage is always tracked and a disabled user can never come back through a cookie.

## 5b. Speed limits and FastTrack (REQUIRED for bandwidth limits to work)

Policies are in **megabits per second** ("2 Mbps" = `rate-limit=2M/2M`, i.e. 2,000,000 bits/s, about 0.25 MB/s of
download). RouterOS enforces them with a per-user queue that the hotspot creates automatically.

**But MikroTik's default firewall includes a FastTrack rule, and FastTrack makes traffic bypass all queues.** With it
enabled, an instructor on a 2 Mbps plan will still speed-test at full line speed (we saw 40+ Mbps). Disable it:

```routeros
/ip firewall filter disable [find action=fasttrack-connection]
```

(In WinBox: IP → Firewall → Filter Rules → the rule `defconf: fasttrack` → *Disable*. The companion
"special dummy rule to show fasttrack counters" can stay.) Trade-off: the router's CPU does a little more work per packet;
a hEX handles a school-sized link comfortably.

The portal checks this for you: `python manage.py check_router` prints a **WARNING** while FastTrack is on, and the admin
*Dashboard / Live monitoring* shows a red "Speed limits are NOT being enforced" banner until you disable the rule.

To confirm limits work: log a phone in and run a speed test - download and upload should stay at or just under the policy
speed (a short burst at the very start of a test is normal). In WinBox → Queues → Simple Queues you will see a dynamic
`<hotspot-username>` queue with `max-limit` = the policy (e.g. `2M/2M`) while that instructor is online.

## 6. Install the custom `login.html`

The file in this repo, `mikrotik/hotspot/login.html`, immediately redirects the phone to:

```
http://192.168.88.5[:port]/portal/login/?mac=$(mac)&ip=$(ip)&dst=$(link-orig-esc)
```

`$(mac)`, `$(ip)` and `$(link-orig-esc)` are filled in by the router when it serves the page.

If your portal address or **port** is different, change `PORTAL_BASE_URL` in `.env` (e.g. `http://192.168.88.5:8088` when port 80 is taken by another app) and regenerate the file, then upload it again:

```
python manage.py make_login_html
```

Upload it:

1. Find the hotspot HTML directory name: `/ip hotspot profile print` → look at `html-directory` for `hsprof1`
   (default `hotspot`).
2. WinBox → **Files**. Open the `hotspot` folder.
3. **Back up the original first:** right-click `login.html` → *Copy* / download it to your PC.
4. Drag `mikrotik/hotspot/login.html` from your PC into the `hotspot` folder in WinBox (overwrite when asked).
   (Alternative: `ftp`/`scp` to the router if you have enabled it.)
5. No restart is needed. To restore the original, upload the backup copy again.

### 6b. Optional but recommended: let instructors get back to their dashboard

On phones the sign-in window **closes by itself** as soon as the phone sees working internet - that is the phone's
behaviour and can't be prevented. Instructors can still reach their dashboard (data used, quota, history) any time on
the school Wi-Fi, even when their quota is used up (they are signed in to the portal but kept offline).
Two helpers make that easy:

1. **`status.html`** - the router shows this page when a connected instructor opens the hotspot address
   (for example `http://login.cdk.local/status`). The generated file `mikrotik/hotspot/status.html` simply redirects to
   the dashboard. Upload it to the same `hotspot` folder as `login.html` (WinBox → Files → `hotspot`; back up the
   original first). *(Not tested on the physical router yet - verify on a phone after uploading.)*
2. **A short name for the portal** (so instructors don't have to remember an IP). On the router:
   ```routeros
   /ip dns static add name=wifi.cdk.local address=192.168.88.5 comment="Portal"
   ```
   Add `wifi.cdk.local` to `ALLOWED_HOSTS` in `.env`, restart the `CDKPortal` service, and instructors can open
   `http://wifi.cdk.local/portal/`. (The walled garden already allows `192.168.88.5`, which covers this name.)

Tell instructors once: *"After you connect, bookmark your dashboard - or add it to your home screen."* The dashboard
page itself shows this tip with the correct address.

## 7. Static IP `192.168.88.5` for the Django server (Windows 10/11)

**Settings UI:** Settings → Network & Internet → Ethernet (or Wi-Fi) → *Edit* next to IP assignment → Manual → IPv4 on:

| Field | Value |
|---|---|
| IP address | `192.168.88.5` |
| Subnet prefix length | `24` (255.255.255.0) |
| Gateway | `192.168.88.1` |
| Preferred DNS | `192.168.88.1` (or 8.8.8.8) |

**Or PowerShell (Run as administrator)** – replace `Ethernet` with your adapter name (`Get-NetAdapter`):

```powershell
New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress 192.168.88.5 -PrefixLength 24 -DefaultGateway 192.168.88.1
Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ServerAddresses 192.168.88.1
```

`192.168.88.5` is outside the DHCP pool (`192.168.88.10–254`), so it will never be handed to a phone.

**Windows Firewall** – allow the portal port on the private network (administrator PowerShell):

```powershell
New-NetFirewallRule -DisplayName "CDK Portal 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private,Domain
```

Also make sure the network is marked **Private** (Settings → Network → Properties), otherwise the rule above doesn't apply.

## 8. Take over the wizard's `test` user

The hotspot wizard created a user called `test`. Django adopts it the first time `test` logs in through the portal
(it overwrites the password with a random one and tags it `cdk-managed`). Nothing else on the router that is not
tagged `comment=cdk-managed` is ever modified.

---

## Verify, in this order

1. **From the Django PC:** `python manage.py check_router` → expect `API connection: OK`, RouterOS `7.20.8`, hotspot server running.
   (Set `MIKROTIK_MOCK=False` in `.env` first.)
2. **Push policies:** *Admin panel → Bandwidth policies → Sync all to router*. On the router:
   `/ip hotspot user profile print` shows `cdk-standard`, `cdk-restricted` with `rate-limit=2M/2M` etc.
3. **Phone test:** connect a phone to the open SSID → the login page of the portal appears → sign in as `test` / `test123`
   → you are online. `/ip hotspot active print` shows the user; the admin panel *Live monitoring* shows it within 5 seconds.
4. **Kick** from *Live monitoring* → the phone is disconnected; opening a site returns it to the login page.
5. **Quota:** set a tiny quota (e.g. 5 MB) on a policy, sync it, log in, download something → the router cuts the user
   and `sync_sessions` records the session as *Quota used up*.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `check_router` says *did not answer in time* | Wrong `MIKROTIK_HOST`, firewall, or `/ip service api` address restriction doesn't include the server. |
| *rejected the API username or password* | `MIKROTIK_USER` / `MIKROTIK_PASSWORD` in `.env` don't match the `djangoapi` user. |
| Phone shows a blank page instead of the login page | Walled-garden entry missing, Windows firewall blocking the portal port, or the server isn't running on `0.0.0.0:<port>00`. |
| Login works in Django but device isn't online | `ip` / `mac` not passed (check `login.html` was uploaded to the right `html-directory`); see the audit log for `login.router_error`. |
| Page loads without styling | You are not loading `/static/` from Django; make sure the server is started from the project folder; WhiteNoise serves `/static/` even with `DEBUG=False` (no `collectstatic` needed). |
| Phone's "sign in to network" popup closes immediately after login | Normal: the OS detected internet. The instructor can reopen the portal at `http://192.168.88.5[:port]/portal/` (add the port only if the portal does not use port 80). |
