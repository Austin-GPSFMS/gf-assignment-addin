"""
GPSFMS GF-assignment API — FastAPI, multi-database.

Endpoints (all POST, JSON body carries the driver's Drive session):

  POST /api/me         {server, database, userName, sessionId}
                       → {userId, userName, displayName, isDriver, current:[{id,name,parent}]}
  POST /api/gf-groups  {server, database, userName, sessionId}
                       → {groups:[{id,name,parent}]}
  POST /api/assign     {server, database, userName, sessionId, groupId}
                       → {ok, current:[...], message}
  GET  /api/health     → {ok, databases:{<db>:{label, gfGroups, assignField}}}

The `database` in the body selects the tenant (see tenants.py): its service
account, GF parent group(s) and assignment field. The same hosted add-in
therefore works in the GPSFMS demo database and in every client database
that is listed in databases.json.

Security model
  • The driver's session is replayed against MyGeotab (`Get User` as the
    driver). A forged/expired session is rejected. The User.id MyGeotab
    returns is the only identity we act on — the add-in cannot assign anyone
    but the logged-in driver.
  • `database` must be a configured tenant. `server` must be a *.geotab.com
    host (no SSRF).
  • `groupId` must be one of the tenant's selectable GF groups (children of
    its gfParentGroups). Nothing else can be written.
  • Every assignment is appended to AUDIT_LOG as a JSON line.

Run locally:  uvicorn app:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from mygeotab.exceptions import MyGeotabException
from pydantic import BaseModel, Field

import gf_logic
from geotab_client import GroupCache, ServiceAccount, SessionInvalid, validate_driver_session
from tenants import Tenant, load_tenants

load_dotenv(Path(__file__).with_name(".env"))
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gf.api")

GF_CACHE_SECONDS = int(os.environ.get("GF_CACHE_SECONDS", "300"))
AUDIT_LOG = Path(os.environ.get("AUDIT_LOG", "audit.jsonl"))
EXTRA_CORS = [o.strip() for o in os.environ.get("EXTRA_CORS_ORIGINS", "").split(",") if o.strip()]

app = FastAPI(title="GPSFMS GF Assignment API", version="1.1.0", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=EXTRA_CORS,
    allow_origin_regex=r"https://([a-z0-9-]+\.)*geotab\.com",
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=600,
)


# --------------------------------------------------------------------------
# Tenants (one service account + group cache per database)
# --------------------------------------------------------------------------
class Runtime:
    def __init__(self, tenant: Tenant, svc: ServiceAccount | None = None):
        self.tenant = tenant
        self.svc = svc or ServiceAccount(tenant.username, tenant.password, tenant.database, tenant.server)
        self.groups = GroupCache(self.svc, GF_CACHE_SECONDS)

    def gf_list(self, force: bool = False) -> list[dict]:
        return gf_logic.build_gf_list(self.groups.groups(force), self.tenant.gf_parent_groups, self.tenant.gf_include_descendants)


# Lazily built so importing the module (tests) doesn't read config or hit MyGeotab.
_runtimes: dict[str, Runtime] | None = None


def runtimes() -> dict[str, Runtime]:
    global _runtimes
    if _runtimes is None:
        _runtimes = {db: Runtime(t) for db, t in load_tenants().items()}
        log.info("tenants: %s", ", ".join(_runtimes))
    return _runtimes


def runtime_for(database: str) -> Runtime:
    rt = runtimes().get(database.strip().lower())
    if not rt:
        raise HTTPException(403, "This database is not enabled for General Foreman assignment.")
    return rt


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class SessionBody(BaseModel):
    server: str = Field(default="my.geotab.com", max_length=120)
    database: str = Field(min_length=1, max_length=120)
    userName: str = Field(min_length=1, max_length=256)
    sessionId: str = Field(min_length=8, max_length=512)


class AssignBody(SessionBody):
    groupId: str = Field(min_length=1, max_length=64)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _identify(body: SessionBody) -> tuple[Runtime, dict]:
    """Validate the driver session; return (runtime, User as the service account sees it)."""
    rt = runtime_for(body.database)
    try:
        as_driver = validate_driver_session(body.server, body.database, body.userName, body.sessionId)
    except SessionInvalid as e:
        log.info("session rejected for %s@%s: %s", body.userName, body.database, e)
        raise HTTPException(401, "Your Geotab Drive session could not be verified. Log out and back in, then try again.")
    user = rt.svc.get_user_by_id(as_driver["id"])
    if not user:
        raise HTTPException(404, "User not found.")
    return rt, user


def _me_payload(rt: Runtime, user: dict, gf_by_id: dict[str, dict]) -> dict:
    return {
        "userId": user["id"],
        "userName": user.get("name") or "",
        "displayName": gf_logic.display_name(user),
        "isDriver": bool(user.get("isDriver")),
        "current": gf_logic.current_gf(user, gf_by_id, rt.tenant.membership_field),
    }


def _audit(event: dict) -> None:
    try:
        event = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError as e:  # never fail the request over the audit file
        log.warning("audit write failed: %s", e)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    out: dict[str, dict] = {}
    ok = True
    try:
        rts = runtimes()
    except Exception as e:  # pragma: no cover - operational
        log.exception("tenant config failed")
        return {"ok": False, "error": str(e)}
    for db, rt in rts.items():
        try:
            out[db] = {"label": rt.tenant.label, "gfGroups": len(rt.gf_list()), "assignField": rt.tenant.membership_field, "emailEnabled": rt.tenant.email_enabled}
        except Exception as e:  # pragma: no cover - operational
            ok = False
            log.exception("health check failed for %s", db)
            out[db] = {"label": rt.tenant.label, "error": str(e)}
    return {"ok": ok, "databases": out}


@app.post("/api/me")
def me(body: SessionBody):
    rt, user = _identify(body)
    gf_by_id = {g["id"]: g for g in rt.gf_list()}
    return _me_payload(rt, user, gf_by_id)


@app.post("/api/gf-groups")
def gf_groups(body: SessionBody):
    rt, _ = _identify(body)  # only authenticated drivers may list
    return {"groups": rt.gf_list()}


@app.post("/api/assign")
def assign(body: AssignBody, request: Request):
    rt, user = _identify(body)
    t = rt.tenant
    gf_by_id = {g["id"]: g for g in rt.gf_list()}
    if body.groupId not in gf_by_id:
        # Maybe the group was just created — refresh once before refusing.
        gf_by_id = {g["id"]: g for g in rt.gf_list(force=True)}
        if body.groupId not in gf_by_id:
            raise HTTPException(400, "That General Foreman is not available for selection.")

    before = gf_logic.current_gf(user, gf_by_id, t.membership_field)
    if len(before) == 1 and before[0]["id"] == body.groupId:
        return {"ok": True, "current": before, "message": "Already assigned."}

    updated = gf_logic.apply_assignment(user, set(gf_by_id), body.groupId, t.assign_field)
    t0 = time.monotonic()
    try:
        rt.svc.set_user(updated)
    except MyGeotabException as e:
        log.warning("User.Set failed for %s@%s: %s", user.get("name"), t.database, e)
        _audit({"event": "assign_failed", "database": t.database, "userId": user["id"], "userName": user.get("name"), "groupId": body.groupId, "error": str(e)})
        raise HTTPException(422, f"MyGeotab refused the change: {getattr(e, 'message', str(e))}")

    fresh = rt.svc.get_user_by_id(user["id"]) or updated
    after = gf_logic.current_gf(fresh, gf_by_id, t.membership_field)
    _audit(
        {
            "event": "assign",
            "database": t.database,
            "userId": user["id"],
            "userName": user.get("name"),
            "from": [g["name"] for g in before],
            "to": [g["name"] for g in after],
            "field": t.membership_field,
            "ip": request.client.host if request.client else None,
            "ms": round((time.monotonic() - t0) * 1000),
        }
    )
    log.info("[%s] assigned %s → %s", t.database, user.get("name"), [g["name"] for g in after])
    return {"ok": True, "current": after}
