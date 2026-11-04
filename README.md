# My General Foreman — Geotab Drive Add-In + Pre-Trip Emailer (ILB)

Drivers open **My General Foreman** in Geotab Drive, tap the General Foreman
they report to, and are moved into that GF's MyGeotab group. A daily job then
emails each General Foreman the list of *their* drivers who completed a
Pre-Trip DVIR that day — the GF compares it against their own crew list.

Three pieces, one repo:

| Piece | Where | Stack |
|---|---|---|
| Drive add-in (driver UI) | `src/`, `dist/`, `config.json` | Vite + React + TypeScript + Zenith, hosted on GitHub Pages |
| Assignment API | `backend/app.py` | FastAPI + `mygeotab`, runs on a GPSFMS Lightsail instance behind Caddy (HTTPS) |
| Pre-trip emailer | `backend/pretrip_email.py` | Python, systemd timer (daily), SMTP |

## Why there's a backend

A Geotab Drive login runs with the driver's own security clearance, which
cannot `Set` a `User` — not even its own record. So the add-in **never writes
to MyGeotab**. It sends the driver's Drive session (`server / database /
userName / sessionId`, from `api.getSession`) plus the chosen `groupId` to the
API. The API:

1. replays that session against MyGeotab (`Get User` *as the driver*) — a
   forged or expired session is rejected; the `User.id` MyGeotab returns is the
   only identity acted on, so a driver can only ever move **themselves**;
2. checks `database` is the one configured and `server` is `*.geotab.com`;
3. checks `groupId` is one of the selectable GF groups (children of
   `GF_PARENT_GROUPS`) — nothing else can be written;
4. uses the GPSFMS **service account** to `Set` the user: every GF group is
   removed from `driverGroups` and the chosen one added (other groups are left
   alone);
5. appends a JSON line to `audit.jsonl` (`who`, `from`, `to`, `ip`, `ms`).

`ASSIGN_FIELD` controls which field is written: `driver` (default —
`driverGroups`, what the DVIR/driver reports key off), `company`
(`companyGroups`, data-access scope) or `both`. If MyGeotab refuses a `Set`
(e.g. "driver groups must be within the user's data access") the add-in shows
Geotab's message verbatim — switch to `both`, or widen the driver's company
groups, and retry.

## Multiple databases (demo first, then clients)

The backend is multi-tenant. `backend/databases.json` lists every MyGeotab
database it serves — the GPSFMS demo database and each client — with its own
service-account credentials, GF parent group(s), assignment field and email
settings (`databases.example.json` shows both). The Drive add-in sends the
driver's `database` with every call, so **one hosted add-in and one backend
serve all of them**; only `config.json` has to be installed in each database.
Shared settings (SMTP, defaults) stay in `.env`; a per-database recipients
file is `recipients.<database>.json`.

### Demo database walkthrough

1. Add the demo database to `databases.json` with `automations@gpsfms.com`
   (the service account must exist in that database too — it's per-database)
   and `"emailEnabled": true`, `fallbackTo` = you, `subjectPrefix` = `[DEMO] …`.
2. Seed the group tree and check what the add-in will show:

   ```bash
   cd /opt/gf-assignment          # or backend/ on your PC with a local .venv
   .venv/bin/python seed_demo.py --database <demo db> --gf "Alex Reyes" --gf "Sam Patel"
   ```

   It creates `General Foremen` under the company group plus the GF groups
   (idempotent), then prints the resolved list. `/api/health` should now show
   that database with the same `gfGroups` count.
3. Make a test driver: a user with *Driver* checked, a Drive password, data
   access = company group, in the demo database. Install `config.json` in the
   demo database (Add-Ins) and open **My General Foreman** in Drive on a
   phone/tablet logged in as that driver. Pick a GF, Save, then confirm the
   user's *Driver groups* changed in MyGeotab and the line appeared in
   `audit.jsonl`.
4. Submit a pre-trip in Drive as that driver on a demo vehicle (the real
   path), or fake one without a tablet:

   ```bash
   .venv/bin/python seed_demo.py --database <demo db> --pretrip driver@demo.com --vehicle "Demo Truck 1"
   .venv/bin/python pretrip_email.py --database <demo db> --dry-run
   ```

   The dry run writes `out/<db>_<date>_<GF>.html` per GF plus the UNASSIGNED
   file; drop `--dry-run` to actually send to yourself.
5. When that all checks out, add the `ilb` entry to `databases.json` with
   `"emailEnabled": false`, restart `gf-api`, install `config.json` in ILB,
   and repeat step 3 with one ILB driver. Flip `emailEnabled` to `true` when
   the GFs are ready for the daily email. The demo entry can stay — set its
   `emailEnabled` to `false` so it stops mailing you.

## MyGeotab setup (one time, per database)

1. **GF groups.** Create a parent group (e.g. `General Foremen`) and put one
   child group per General Foreman under it (`Bill Whalen`, `Mike Burke`, …).
   Existing per-GF groups can simply be moved under that parent. One parent
   per division also works: `GF_PARENT_GROUPS=General Foremen - ID,General Foremen - OR`
   (the add-in shows the parent as a subtitle when there is more than one).
2. **Service account** (per Geotab's [service-accounts guide](https://support.geotab.com/help/mygeotab/access-and-administration/database-administration/service-accounts)):
   Users & Drivers → Add → Authentication type **Service Account**. GPSFMS
   uses the shared account `automations@gpsfms.com`, created separately in
   each database it serves (Geotab recommends one per solution; split later if
   the account's clearance or call volume grows — it is only a
   `databases.json` value). Service accounts are
   API-only — UI login errors by design. Password: ≥ 8 chars, not a top-100
   password, must not contain the username; unique per account. Data access:
   the company group. Clearance: clone **Supervisor** into a sub-clearance and
   keep only what this solution uses, then document it:

   | Needed for | Feature (security identifier) |
   |---|---|
   | read drivers, verify who is asking | View users/drivers (`ViewUser`) |
   | write the GF group onto the driver | Administer users/drivers (`EditUser`) |
   | list the General Foremen tree | View groups (`ViewGroups`) |
   | emailer: vehicle names | View devices (`DeviceList` / `ViewDevice`) |
   | emailer: pre-trip inspections | View DVIR logs (`ViewDVIRLog`) |
   | `seed_demo.py` only (demo database) | Administer groups, Administer DVIR logs |

   Sessions expire every 14 days; `geotab_client.py` re-authenticates on the
   `Incorrect Login Credentials` error automatically. To rotate the password
   later, change it in the UI (or via `Set User` with the account's own creds)
   and update `.env`. Since GPSFMS adds this account as the Partner, record
   ILB's consent for it.
3. **Drivers** must be flagged *Driver* on their user with a Geotab Drive
   login. The add-in warns if the signed-in user is not a driver.
4. **Install the add-in** (Administration → System → System Settings →
   Add-Ins → New → paste `config.json`). `"path": "DriveAppLink/"` puts it in
   the Drive app menu. Set `"onStartup": true` if you want drivers who have no
   GF yet to get a Drive notification at login nudging them to pick one.

## Build & host the add-in

```powershell
cd C:\Users\User\Projects\gf-assignment-addin
npm install
npm run build          # tsc + vite → dist/
npm test               # Playwright smoke test against dist/ (needs playwright installed)
```

1. Set `BACKEND_URL` in `src/config.ts` to the API's HTTPS address
   (`https://gf.gpsfms.com`) **before** building.
2. Push the folder (including `dist/` — don't gitignore it) to a GitHub repo
   named `gf-assignment-addin`; enable Pages from `main` / root.
3. `config.json` already points at
   `https://austin-gpsfms.github.io/gf-assignment-addin/dist/index.html`; the
   Vite `base` in `vite.config.ts` must match that host (Drive injects the
   add-in into its own page, so asset URLs have to be absolute).

Opening `dist/index.html` directly shows a *Standalone preview* banner — the
page only loads data inside Drive.

## Deploy the API + emailer (Lightsail, console only)

1. Lightsail → Create instance → Linux/Unix → **Ubuntu 24.04**, smallest
   plan is plenty. Attach a **static IP**. Networking tab: open **443** (80 is
   already open).
2. DNS: `A` record `gf.gpsfms.com` → the static IP.
3. Use the browser SSH button, upload the repo (or `git clone` it), then:

```bash
cd ~/gf-assignment-addin
sudo bash deploy/lightsail-setup.sh gf.gpsfms.com
sudo nano /opt/gf-assignment/databases.json       # one entry per database (demo, ilb, …)
sudo nano /opt/gf-assignment/.env                 # SMTP + shared defaults
sudo nano /opt/gf-assignment/recipients.ilb.json  # GF name → email(s), per database
sudo systemctl restart gf-api
curl -s https://gf.gpsfms.com/api/health          # {"ok":true,"databases":{"ilb":{"gfGroups":8,...},...}}
sudo -u gfapi /opt/gf-assignment/.venv/bin/python /opt/gf-assignment/pretrip_email.py --dry-run --database ilb
```

The setup script installs Caddy (automatic Let's Encrypt HTTPS), a
`gf-api` systemd service (uvicorn on localhost:8000) and a `gf-pretrip.timer`
that runs the emailer daily at **10:00** server time (the script sets the
instance timezone to `REPORT_TZ`, default `America/Los_Angeles`; pass
`EMAIL_TIME=09:30` to change it). `deploy/Dockerfile` is the container
alternative.

Logs: `journalctl -u gf-api -f`, `journalctl -u gf-pretrip`. Audit trail:
`/opt/gf-assignment/audit.jsonl`.

## The daily email

`pretrip_email.py` runs once per database with `emailEnabled` (or just
`--database X`). For each it pulls `Group`, `User`, `Device` and the day's
`DVIRLog` (local midnight→midnight in that database's `reportTz`), keeps
`logType == PreTrip`, and
groups the logs by the driver's GF `driverGroups` membership. One email per
GF: *driver · vehicle · pre-trip time · defects · safe to operate*, with
"N of M assigned drivers" in the subtitle. GFs with zero pre-trips still get
an email (so silence is never ambiguous) unless `--skip-empty`.

Drivers who did a pre-trip but never picked a GF appear in a separate
**UNASSIGNED** email to `UNASSIGNED_TO` (default `FALLBACK_TO`) — that is the
list to chase.

Recipients per GF: `recipients.<database>.json` (else `recipients.json`) →
a MyGeotab user whose first + last name equals the group name and whose user
name is an email → that database's `fallbackTo`. `ccAll` is copied on
everything.

```bash
python pretrip_email.py                        # today, every emailEnabled database, send
python pretrip_email.py --database gpsfms_pilot # one database
python pretrip_email.py --date 2026-10-26      # a past day
python pretrip_email.py --dry-run              # render to backend/out/<db>_*.html, send nothing
python pretrip_email.py --only "Bill Whalen"
```

## Project layout

```
config.json                 Drive add-in manifest (DriveAppLink)
src/
  main.tsx                  lifecycle: initialize / focus / blur / startup
  App.tsx                   page: who am I, current GF, picker, Save
  components/GfPicker.tsx   searchable tap-list
  api/backend.ts            fetchSession + /api/me, /api/gf-groups, /api/assign
  config.ts                 BACKEND_URL
  styles.css                Zenith tokens + mobile chrome
test/smoke.mjs              Playwright smoke test of dist/
backend/
  app.py                    FastAPI API (multi-database)
  tenants.py                per-database config: databases.json → Tenant, .env fallback
  geotab_client.py          driver-session validation + service-account wrapper
  gf_logic.py               pure group/assignment/roll-up logic (unit-tested)
  pretrip_email.py          daily emailer (per database)
  seed_demo.py              create GF groups / a test pre-trip in a demo database
  tests/                    pytest (33 tests, no network)
  .env.example, databases.example.json, recipients.example.json, assets/gpsfms-logo-white.png
deploy/
  lightsail-setup.sh        Ubuntu one-shot: venv + systemd + timer + Caddy
  Dockerfile                container alternative
```

Backend tests: `cd backend && pip install -r requirements.txt && python -m pytest -q`.

## API calls

Per add-in open: 1 `Get User` as the driver (session check) ×2 (`/me`,
`/gf-groups` — each endpoint validates independently), 1 `Get User` by id
via the service account, `Get Group` once per 5 minutes (cached, per
database). Per save: the same plus 1 `Set User` + 1 `Get User`. The emailer:
4 `Get`s per database per run.

## Known limits / things verified only from docs

- Drive add-in lifecycle (`initialize/focus/blur`, `onStartup` → `startup`)
  and `"path": "DriveAppLink/"` are per developers.geotab.com; the
  `api.mobile.notification.notify` call used by `startup` is wrapped in
  try/catch because its exact signature isn't documented. Everything in the
  add-in was exercised in a browser with a mocked `api` + backend, not yet in
  the Drive app itself.
- Whether MyGeotab requires `driverGroups` to sit inside the user's
  `companyGroups` scope depends on the database's user configuration; the API
  surfaces Geotab's error and `ASSIGN_FIELD=both` is the fallback.
- `DVIRLog` defects are read from `dvirDefects` (current API) with a fallback
  to the legacy `defects` key.
