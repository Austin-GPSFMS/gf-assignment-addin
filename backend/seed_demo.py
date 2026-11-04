#!/usr/bin/env python3
"""
Seed a demo/test database for the My General Foreman add-in.

  python seed_demo.py --database gpsfms_demo
      Creates (idempotently) the "General Foremen" parent group under the
      company group and a few demo GF groups beneath it. Prints the tree.

  python seed_demo.py --database gpsfms_demo --gf "Alex Reyes" --gf "Sam Patel"
      Use your own GF names instead of the defaults.

  python seed_demo.py --database gpsfms_demo --pretrip driver@demo.com --vehicle "Demo Truck 1"
      Also writes a clean (no-defect) PreTrip DVIRLog for that driver on that
      vehicle right now, so the emailer has something to report without a
      tablet. The real add-in test is still a pre-trip submitted from Geotab
      Drive; this is only for exercising the email.

Credentials come from databases.json / .env (see tenants.py). The service
account needs Administer groups for the group step and Administer DVIR logs
for --pretrip.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from mygeotab.exceptions import MyGeotabException

import gf_logic
from geotab_client import ServiceAccount
from tenants import load_tenants

load_dotenv(Path(__file__).with_name(".env"))
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gf.seed")

DEFAULT_GFS = ["Bill Whalen", "Mike Burke", "Dan Haggard"]


def ensure_group(svc: ServiceAccount, by_name: dict[str, dict], name: str, parent_id: str) -> dict:
    g = by_name.get(name.lower())
    if g:
        log.info("exists: %s (%s)", name, g["id"])
        return g
    gid = svc.call("Add", typeName="Group", entity={"name": name, "parent": {"id": parent_id}, "children": []})
    log.info("created: %s (%s) under %s", name, gid, parent_id)
    g = {"id": gid, "name": name, "children": []}
    by_name[name.lower()] = g
    return g


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", required=True)
    ap.add_argument("--parent", default=None, help="Parent group name (default: the tenant's first gfParentGroups entry)")
    ap.add_argument("--gf", action="append", help="GF group name (repeatable). Default: three demo names")
    ap.add_argument("--pretrip", metavar="DRIVER_EMAIL", help="Also add a clean PreTrip DVIRLog for this driver")
    ap.add_argument("--vehicle", help="Device name for --pretrip")
    args = ap.parse_args(argv)

    tenants = load_tenants()
    t = tenants.get(args.database.lower())
    if not t:
        log.error("database %r is not configured in databases.json / .env (known: %s)", args.database, ", ".join(tenants))
        return 2
    svc = ServiceAccount(t.username, t.password, t.database, t.server)
    parent_name = args.parent or t.gf_parent_groups[0]

    groups = svc.get("Group")
    by_name = {(g.get("name") or "").lower(): g for g in groups}
    parent = ensure_group(svc, by_name, parent_name, "GroupCompanyId")
    for name in args.gf or DEFAULT_GFS:
        ensure_group(svc, by_name, name, parent["id"])

    gf = gf_logic.build_gf_list(svc.get("Group"), [parent_name], t.gf_include_descendants)
    print(f"\n{t.database}: {parent_name} → {len(gf)} General Foreman group(s)")
    for g in gf:
        print(f"  • {g['name']}  ({g['id']})")

    if args.pretrip:
        if not args.vehicle:
            log.error("--pretrip needs --vehicle <device name>")
            return 2
        users = svc.get("User", name=args.pretrip, resultsLimit=1)
        devices = svc.get("Device", name=args.vehicle, resultsLimit=1)
        if not users or not devices:
            log.error("driver %r or vehicle %r not found", args.pretrip, args.vehicle)
            return 2
        user, device = users[0], devices[0]
        if not user.get("isDriver"):
            log.warning("%s is not flagged as a driver — the pre-trip will still be created but won't look like a Drive submission", args.pretrip)
        entity = {
            "dateTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "device": {"id": device["id"]},
            "driver": {"id": user["id"]},
            "logType": "PreTrip",
            "isSafeToOperate": True,
            "isInspectedByDriver": True,
            "driverRemark": "Seeded by seed_demo.py",
            "defects": [],
        }
        try:
            log_id = svc.call("Add", typeName="DVIRLog", entity=entity)
            print(f"\nPreTrip DVIRLog {log_id} added for {gf_logic.display_name(user)} on {device.get('name')}")
        except MyGeotabException as e:
            # Common causes: previous uncertified DVIRLog on this device, or the
            # database requires a defectList on every log — see dvir-photo-poc/HANDOFF.md.
            log.error("DVIRLog Add refused: %s", getattr(e, "message", e))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
