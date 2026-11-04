"""
Per-database ("tenant") configuration.

One backend serves any number of MyGeotab databases — the GPSFMS demo
database and ILB side by side, and the next client after that. The Drive
add-in sends the driver's `database` with every request and the backend
picks the matching tenant: its own service-account credentials, GF parent
group(s), assignment field and email settings.

Configuration precedence
  1. `databases.json` next to this file (git-ignored; see databases.example.json)
     — one key per database name, lower-case.
  2. If that file is absent, a single tenant is built from the GEOTAB_* /
     GF_* / emailer variables in `.env` (the original single-database setup).

Any per-tenant key left out of databases.json falls back to the `.env`
default of the same meaning, so shared settings (SMTP, CC_ALL, REPORT_TZ…)
only need to be set once.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATABASES_FILE = Path(os.environ.get("DATABASES_FILE", HERE / "databases.json"))


def _split(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _bool(v, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _list(v, default: list[str]) -> list[str]:
    if v is None:
        return default
    if isinstance(v, str):
        return _split(v)
    return [str(x).strip() for x in v if str(x).strip()]


@dataclass
class Tenant:
    database: str
    username: str
    password: str
    server: str | None = None
    label: str = ""
    gf_parent_groups: list[str] = field(default_factory=lambda: ["General Foremen"])
    gf_include_descendants: bool = False
    assign_field: str = "driver"  # driver | company | both
    # emailer
    email_enabled: bool = True
    recipients_file: str | None = None
    fallback_to: list[str] = field(default_factory=list)
    unassigned_to: list[str] = field(default_factory=list)
    cc_all: list[str] = field(default_factory=list)
    subject_prefix: str = "Pre-Trip Completed"
    report_tz: str = "America/Los_Angeles"

    @property
    def membership_field(self) -> str:
        return "companyGroups" if self.assign_field == "company" else "driverGroups"

    def recipients_path(self) -> Path:
        """recipients file: explicit → recipients.<db>.json → recipients.json"""
        if self.recipients_file:
            p = Path(self.recipients_file)
            return p if p.is_absolute() else HERE / p
        per_db = HERE / f"recipients.{self.database}.json"
        return per_db if per_db.exists() else HERE / "recipients.json"


def _env_defaults() -> dict:
    e = os.environ.get
    return {
        "username": e("GEOTAB_USERNAME"),
        "password": e("GEOTAB_PASSWORD"),
        "server": e("GEOTAB_SERVER") or None,
        "gf_parent_groups": _split(e("GF_PARENT_GROUPS")) or ["General Foremen"],
        "gf_include_descendants": _bool(e("GF_INCLUDE_DESCENDANTS")),
        "assign_field": (e("ASSIGN_FIELD") or "driver").strip().lower(),
        "email_enabled": _bool(e("EMAIL_ENABLED"), True),
        "recipients_file": e("RECIPIENTS_FILE") or None,
        "fallback_to": _split(e("FALLBACK_TO")),
        "unassigned_to": _split(e("UNASSIGNED_TO")),
        "cc_all": _split(e("CC_ALL")),
        "subject_prefix": e("SUBJECT_PREFIX") or "Pre-Trip Completed",
        "report_tz": e("REPORT_TZ") or "America/Los_Angeles",
    }


def _tenant_from(database: str, raw: dict, defaults: dict) -> Tenant:
    g = lambda k, *alts: next((raw[a] for a in (k, *alts) if a in raw and raw[a] not in (None, "")), None)  # noqa: E731
    username = g("username") or defaults["username"]
    password = g("password") or defaults["password"]
    if not (username and password):
        raise RuntimeError(f"database {database!r}: username/password missing (databases.json or GEOTAB_USERNAME/GEOTAB_PASSWORD)")
    t = Tenant(
        database=database.lower(),
        username=username,
        password=password,
        server=g("server") or defaults["server"],
        label=g("label") or database,
        gf_parent_groups=_list(g("gfParentGroups", "gf_parent_groups"), defaults["gf_parent_groups"]),
        gf_include_descendants=_bool(g("gfIncludeDescendants", "gf_include_descendants"), defaults["gf_include_descendants"]),
        assign_field=(g("assignField", "assign_field") or defaults["assign_field"]).strip().lower(),
        email_enabled=_bool(g("emailEnabled", "email_enabled"), defaults["email_enabled"]),
        recipients_file=g("recipientsFile", "recipients_file") or defaults["recipients_file"],
        fallback_to=_list(g("fallbackTo", "fallback_to"), defaults["fallback_to"]),
        unassigned_to=_list(g("unassignedTo", "unassigned_to"), defaults["unassigned_to"]),
        cc_all=_list(g("ccAll", "cc_all"), defaults["cc_all"]),
        subject_prefix=g("subjectPrefix", "subject_prefix") or defaults["subject_prefix"],
        report_tz=g("reportTz", "report_tz") or defaults["report_tz"],
    )
    if t.assign_field not in {"driver", "company", "both"}:
        raise RuntimeError(f"database {database!r}: assignField must be driver|company|both")
    return t


def load_tenants() -> dict[str, Tenant]:
    """{database_lower: Tenant}. Raises RuntimeError on bad/missing config."""
    defaults = _env_defaults()
    if DATABASES_FILE.exists():
        data = json.loads(DATABASES_FILE.read_text(encoding="utf-8"))
        tenants = {}
        for db, raw in data.items():
            if db.startswith("_"):
                continue
            if not isinstance(raw, dict):
                raise RuntimeError(f"databases.json: entry {db!r} must be an object")
            tenants[db.lower()] = _tenant_from(db, raw, defaults)
        if not tenants:
            raise RuntimeError(f"{DATABASES_FILE} has no databases")
        return tenants

    db = os.environ.get("GEOTAB_DATABASE")
    if not db:
        raise RuntimeError("no databases.json and GEOTAB_DATABASE not set")
    allowed = {d.lower() for d in _split(os.environ.get("ALLOWED_DATABASES"))}
    if allowed and db.lower() not in allowed:
        raise RuntimeError("GEOTAB_DATABASE is not in ALLOWED_DATABASES")
    return {db.lower(): _tenant_from(db, {}, defaults)}
