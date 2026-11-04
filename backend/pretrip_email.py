#!/usr/bin/env python3
"""
Daily "who completed their pre-trip" email, one per General Foreman.

For each GF group (children of GF_PARENT_GROUPS) the email lists every
driver assigned to that GF who submitted a Pre-Trip DVIR on the report day:
driver, vehicle, time, defects, safe-to-operate. The GF compares that list
to their own crew list — drivers who never opened the Drive add-in to pick a
GF can't be attributed, so a separate "unassigned" summary goes to
UNASSIGNED_TO / FALLBACK_TO listing pre-trips by drivers with no GF yet.

Recipients per GF, in order of precedence:
  1. recipients.json  {"Bill Whalen": ["bwhalen@…"]}
  2. a MyGeotab user whose first+last name equals the group name and whose
     user name looks like an email
  3. FALLBACK_TO

Runs for every database in databases.json with emailEnabled (or the single
.env database). Per-database settings — GF parent group, report timezone,
recipients file, fallback/unassigned/CC addresses, subject prefix — come
from tenants.py; SMTP is shared.

Usage
  python pretrip_email.py                      # today, every enabled database, send
  python pretrip_email.py --database gpsfms_pilot
  python pretrip_email.py --date 2026-10-26
  python pretrip_email.py --dry-run            # write HTML to out/, send nothing
  python pretrip_email.py --only "Bill Whalen"
  python pretrip_email.py --skip-empty         # don't email GFs with zero pre-trips

Schedule it once a day after the start of shift (see deploy/).
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import smtplib
import sys
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

import gf_logic
from geotab_client import ServiceAccount
from tenants import Tenant, load_tenants

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gf.email")

LOGO = HERE / "assets" / "gpsfms-logo-white.png"

NAVY, DARK, BLUE, LIGHT = "#25477B", "#1C2B39", "#0084C2", "#F4F4F4"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _split(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def day_window(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def fetch(svc, t: Tenant, d: date):
    groups = svc.get("Group")
    gf = gf_logic.build_gf_list(groups, t.gf_parent_groups, t.gf_include_descendants)
    gf_by_id = {g["id"]: g for g in gf}
    users = svc.get("User")
    users_by_id = {u["id"]: u for u in users}
    devices_by_id = {dv["id"]: dv for dv in svc.get("Device")}
    frm, to = day_window(d, ZoneInfo(t.report_tz))
    logs = svc.get("DVIRLog", fromDate=frm.isoformat(), toDate=to.isoformat(), resultsLimit=50000)
    log.info("[%s] groups=%d gf=%d users=%d devices=%d dvirLogs=%d (%s → %s)", t.database, len(groups), len(gf), len(users), len(devices_by_id), len(logs), frm, to)
    return gf, gf_by_id, users_by_id, devices_by_id, logs


# --------------------------------------------------------------------------
# Recipients
# --------------------------------------------------------------------------
def load_recipient_map(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k.strip().lower(): (v if isinstance(v, list) else [v]) for k, v in data.items() if not k.startswith("_")}


def resolve_recipients(gf_name: str, users_by_id: dict[str, dict], rmap: dict[str, list[str]], fallback: list[str] | None = None) -> tuple[list[str], str]:
    key = gf_name.strip().lower()
    if key in rmap and rmap[key]:
        return rmap[key], "recipients file"
    for u in users_by_id.values():
        if gf_logic.display_name(u).strip().lower() == key and EMAIL_RE.match(u.get("name") or ""):
            return [u["name"]], "MyGeotab user"
    return (fallback if fallback is not None else _split(os.environ.get("FALLBACK_TO"))), "fallbackTo"


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def _fmt_time(t: datetime | None) -> str:
    return t.strftime("%-I:%M %p") if t else "—"


def _rows_html(rows: list[dict]) -> str:
    out = []
    for i, r in enumerate(rows):
        bg = LIGHT if i % 2 else "#FFFFFF"
        defects = f'<span style="color:#b42318;font-weight:500">{r["defects"]}</span>' if r["defects"] else "0"
        safe = "Yes" if r["safe"] is True else ("<span style=\"color:#b42318;font-weight:500\">No</span>" if r["safe"] is False else "—")
        out.append(
            f'<tr style="background:{bg}">'
            f'<td style="padding:8px 10px;border-bottom:1px solid #e3e6ea">{html.escape(r["driver"])}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #e3e6ea">{html.escape(str(r["vehicle"]))}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #e3e6ea;white-space:nowrap">{_fmt_time(r["time"])}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #e3e6ea;text-align:center">{defects}</td>'
            f'<td style="padding:8px 10px;border-bottom:1px solid #e3e6ea;text-align:center">{safe}</td>'
            "</tr>"
        )
    return "".join(out)


def render_email(title: str, subtitle: str, rows: list[dict], note: str | None, logo_cid: str | None) -> str:
    th = f'style="padding:9px 10px;background:{NAVY};color:#fff;text-align:left;font-weight:700;font-size:13px"'
    logo = f'<img src="cid:{logo_cid}" alt="GPSFMS" width="150" style="width:150px;height:auto;display:block">' if logo_cid else '<span style="color:#fff;font-weight:700;font-size:18px">GPSFMS</span>'
    body = (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:14px;color:{DARK}">'
        f"<thead><tr><th {th}>Driver</th><th {th}>Vehicle</th><th {th}>Pre-trip time</th>"
        f'<th {th.replace("text-align:left", "text-align:center")}>Defects</th><th {th.replace("text-align:left", "text-align:center")}>Safe to operate</th></tr></thead>'
        f"<tbody>{_rows_html(rows)}</tbody></table>"
        if rows
        else f'<p style="margin:0;padding:18px;background:{LIGHT};border-radius:6px;color:{DARK}">No pre-trip inspections were recorded for your drivers today.</p>'
    )
    note_html = f'<p style="margin:16px 0 0;font-size:13px;color:#5b6572">{note}</p>' if note else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title></head>
<body style="margin:0;padding:0;background:#eef1f4;font-family:Roboto,'Segoe UI',Helvetica,Arial,sans-serif;color:{DARK}">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef1f4"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="640" cellspacing="0" cellpadding="0" style="max-width:640px;width:100%;background:#fff;border-radius:8px;overflow:hidden">
  <tr><td style="background:{NAVY};padding:18px 24px">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
      <td style="vertical-align:middle">{logo}</td>
      <td style="vertical-align:middle;text-align:right;color:#fff;font-size:13px">Daily pre-trip report</td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:22px 24px 6px">
    <h1 style="margin:0;font-size:20px;color:{NAVY}">{html.escape(title)}</h1>
    <p style="margin:6px 0 0;font-size:14px;color:{BLUE};font-weight:500">{html.escape(subtitle)}</p>
  </td></tr>
  <tr><td style="padding:12px 24px 24px">{body}{note_html}</td></tr>
  <tr><td style="padding:14px 24px;background:{LIGHT};font-size:11px;color:#5b6572;line-height:1.5">
    GPS Fleet Management Solutions · 315 Hwy 70 East, Suite E — Garner, NC 27529 · www.gpsfms.com · Geotab Authorized Partner &amp; Reseller<br>
    Drivers pick their General Foreman in Geotab Drive → <b>My General Foreman</b>. Questions: support@gpsfms.com
  </td></tr>
</table></td></tr></table></body></html>"""


# --------------------------------------------------------------------------
# Send
# --------------------------------------------------------------------------
def build_message(subject: str, to: list[str], cc: list[str], html_body: str, text_body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM") or formataddr(("GPSFMS Reports", os.environ.get("SMTP_USER", "")))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Message-ID"] = make_msgid(domain="gpsfms.com")
    msg.set_content(text_body)
    logo_cid = None
    if LOGO.exists():
        logo_cid = make_msgid(domain="gpsfms.com")[1:-1]
        html_body = html_body.replace("cid:__LOGO__", f"cid:{logo_cid}")
    msg.add_alternative(html_body, subtype="html")
    if logo_cid:
        msg.get_payload()[1].add_related(LOGO.read_bytes(), maintype="image", subtype="png", cid=f"<{logo_cid}>")
    return msg


def send(msg: EmailMessage) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASSWORD")
    starttls = os.environ.get("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}
    with smtplib.SMTP(host, port, timeout=30) as s:
        if starttls:
            s.starttls()
        if user and pw:
            s.login(user, pw)
        s.send_message(msg)


def text_version(title: str, subtitle: str, rows: list[dict]) -> str:
    lines = [title, subtitle, ""]
    if not rows:
        lines.append("No pre-trip inspections were recorded for your drivers today.")
    for r in rows:
        lines.append(f"- {r['driver']} — {r['vehicle']} — {_fmt_time(r['time'])} — defects: {r['defects']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run_tenant(t: Tenant, d: date, args, out_dir: Path) -> int:
    """Send (or dry-run) the GF emails for one database. Returns emails sent."""
    tz = ZoneInfo(t.report_tz)
    svc = ServiceAccount(t.username, t.password, t.database, t.server)
    gf, gf_by_id, users_by_id, devices_by_id, logs = fetch(svc, t, d)
    if not gf:
        log.error("[%s] no General Foreman groups found under %s — nothing to send", t.database, t.gf_parent_groups)
        return 0

    field = t.membership_field
    by_gf, unassigned = gf_logic.rollup_pretrips(logs, users_by_id, devices_by_id, gf_by_id, field, lambda x: x.astimezone(tz))
    rpath = t.recipients_path()
    rmap = load_recipient_map(rpath)
    day_label = d.strftime("%A, %B %-d, %Y")
    tag = re.sub(r"[^A-Za-z0-9]+", "_", t.database)
    sent = 0

    for g in gf:
        if args.only and g["name"].strip().lower() != args.only.strip().lower():
            continue
        rows = by_gf.get(g["id"], [])
        if args.skip_empty and not rows:
            continue
        drivers_in_gf = sum(1 for u in users_by_id.values() if any(r.get("id") == g["id"] for r in (u.get(field) or [])))
        to, source = resolve_recipients(g["name"], users_by_id, rmap, t.fallback_to)
        title = f"Pre-trip inspections completed — {g['name']}"
        subtitle = f"{day_label} · {len(rows)} of {drivers_in_gf} assigned driver(s)"
        note = "Drivers not on this list either haven't completed a pre-trip today or haven't selected you as their General Foreman in Geotab Drive."
        html_body = render_email(title, subtitle, rows, note, "__LOGO__")
        subject = f"{t.subject_prefix} — {g['name']} — {d.isoformat()}"
        if args.dry_run:
            path = out_dir / f"{tag}_{d.isoformat()}_{re.sub(r'[^A-Za-z0-9]+', '_', g['name'])}.html"
            path.write_text(html_body, encoding="utf-8")
            print(f"[dry-run] [{t.database}] {g['name']}: {len(rows)} pre-trip(s) → {to or '(no recipients)'} via {source} → {path.name}")
            continue
        if not to:
            log.warning("[%s] no recipients for %s (set %s or fallbackTo) — skipped", t.database, g["name"], rpath.name)
            continue
        send(build_message(subject, to, t.cc_all, html_body, text_version(title, subtitle, rows)))
        sent += 1
        log.info("[%s] sent %s → %s (%s)", t.database, g["name"], to, source)

    # Unassigned summary
    if unassigned and not args.only:
        to = t.unassigned_to or t.fallback_to
        title = "Pre-trips by drivers with no General Foreman selected"
        subtitle = f"{day_label} · {len(unassigned)} driver(s)"
        note = "These drivers completed a pre-trip but have not picked a General Foreman in Geotab Drive, so they don't appear on any GF's report."
        html_body = render_email(title, subtitle, unassigned, note, "__LOGO__")
        subject = f"{t.subject_prefix} — UNASSIGNED drivers — {d.isoformat()}"
        if args.dry_run:
            path = out_dir / f"{tag}_{d.isoformat()}_UNASSIGNED.html"
            path.write_text(html_body, encoding="utf-8")
            print(f"[dry-run] [{t.database}] UNASSIGNED: {len(unassigned)} → {to or '(no recipients)'} → {path.name}")
        elif to:
            send(build_message(subject, to, t.cc_all, html_body, text_version(title, subtitle, unassigned)))
            sent += 1
            log.info("[%s] sent unassigned summary → %s", t.database, to)
        else:
            log.warning("[%s] %d unassigned pre-trips but unassignedTo/fallbackTo not set", t.database, len(unassigned))
    return sent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", help="Only this database (default: every database with emailEnabled)")
    ap.add_argument("--date", help="Report day YYYY-MM-DD in the database's reportTz (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="Render to out/ and print recipients; send nothing")
    ap.add_argument("--only", help="Only this General Foreman group name")
    ap.add_argument("--skip-empty", action="store_true", help="Skip GFs with no completed pre-trips")
    args = ap.parse_args(argv)

    tenants = load_tenants()
    if args.database:
        t = tenants.get(args.database.strip().lower())
        if not t:
            log.error("database %r is not configured (known: %s)", args.database, ", ".join(tenants))
            return 2
        selected = [t]
    else:
        selected = [t for t in tenants.values() if t.email_enabled]
        if not selected:
            log.error("no database has emailEnabled — nothing to do")
            return 2

    out_dir = HERE / "out"
    if args.dry_run:
        out_dir.mkdir(exist_ok=True)

    total = 0
    failures = 0
    for t in selected:
        d = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo(t.report_tz)).date()
        try:
            total += run_tenant(t, d, args, out_dir)
        except Exception:  # one bad database must not block the others
            failures += 1
            log.exception("[%s] failed", t.database)

    log.info("done: %d email(s) sent across %d database(s)%s", total, len(selected), f", {failures} failed" if failures else "")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
