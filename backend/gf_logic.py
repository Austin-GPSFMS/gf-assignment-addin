"""
Pure, side-effect-free helpers shared by the API and the emailer.

Everything here takes plain MyGeotab entity dicts (as returned by the
`mygeotab` client) and returns plain data, so it is unit-testable without
a database.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

Group = dict
GroupRef = dict  # {"id": "..."}


# --------------------------------------------------------------------------
# Group tree
# --------------------------------------------------------------------------
def index_groups(groups: Iterable[Group]) -> dict[str, Group]:
    return {g["id"]: g for g in groups if g.get("id")}


def find_parent_groups(groups_by_id: dict[str, Group], parents_cfg: Iterable[str]) -> list[Group]:
    """Resolve each configured parent by exact id or case-insensitive name."""
    wanted = [p.strip() for p in parents_cfg if p and p.strip()]
    by_name: dict[str, list[Group]] = defaultdict(list)
    for g in groups_by_id.values():
        by_name[(g.get("name") or "").strip().lower()].append(g)

    found: list[Group] = []
    seen: set[str] = set()
    for w in wanted:
        hits = [groups_by_id[w]] if w in groups_by_id else by_name.get(w.lower(), [])
        for g in hits:
            if g["id"] not in seen:
                seen.add(g["id"])
                found.append(g)
    return found


def gf_groups_under(groups_by_id: dict[str, Group], parent: Group, include_descendants: bool = False) -> list[Group]:
    """Direct children of `parent` (or all descendants), sorted by name."""
    out: list[Group] = []
    stack = [c["id"] for c in parent.get("children") or []]
    seen: set[str] = set()
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        g = groups_by_id.get(cid)
        if not g:
            continue
        out.append(g)
        if include_descendants:
            stack.extend(c["id"] for c in g.get("children") or [])
    out.sort(key=lambda g: (g.get("name") or "").lower())
    return out


def build_gf_list(groups: Iterable[Group], parents_cfg: Iterable[str], include_descendants: bool = False) -> list[dict]:
    """[{id, name, parent}] of selectable General Foreman groups."""
    by_id = index_groups(groups)
    result: list[dict] = []
    seen: set[str] = set()
    for parent in find_parent_groups(by_id, parents_cfg):
        for g in gf_groups_under(by_id, parent, include_descendants):
            if g["id"] in seen:
                continue
            seen.add(g["id"])
            result.append({"id": g["id"], "name": g.get("name") or g["id"], "parent": parent.get("name") or parent["id"]})
    result.sort(key=lambda r: r["name"].lower())
    return result


# --------------------------------------------------------------------------
# User group membership
# --------------------------------------------------------------------------
def current_gf(user: dict, gf_by_id: dict[str, dict], field: str = "driverGroups") -> list[dict]:
    """Which GF groups (from the selectable list) the user is in, via `field`."""
    refs = user.get(field) or []
    return [gf_by_id[r["id"]] for r in refs if r.get("id") in gf_by_id]


def parent_map(groups_by_id: dict[str, Group]) -> dict[str, str]:
    """{child_id: parent_id} derived from each group's `children` list."""
    out: dict[str, str] = {}
    for g in groups_by_id.values():
        for c in g.get("children") or []:
            if c.get("id"):
                out[c["id"]] = g["id"]
    return out


def ancestors(groups_by_id: dict[str, Group], group_id: str) -> set[str]:
    parents = parent_map(groups_by_id)
    out: set[str] = set()
    cur = parents.get(group_id)
    while cur and cur not in out:
        out.add(cur)
        cur = parents.get(cur)
    return out


def descendants(groups_by_id: dict[str, Group], group_id: str) -> set[str]:
    out: set[str] = set()
    stack = [c["id"] for c in (groups_by_id.get(group_id) or {}).get("children") or []]
    while stack:
        cid = stack.pop()
        if cid in out:
            continue
        out.add(cid)
        stack.extend(c["id"] for c in (groups_by_id.get(cid) or {}).get("children") or [])
    return out


def replace_gf_membership(
    refs: Iterable[GroupRef] | None, gf_ids: set[str], selected_id: str, conflicts: set[str] | frozenset[str] = frozenset()
) -> list[GroupRef]:
    """
    New group-ref list: every existing group kept (order preserved) EXCEPT
    other GF groups and anything in `conflicts` (ancestors/descendants of the
    selected group — MyGeotab refuses "groups that have a parent-child
    relationship" on one entity), then the selected GF group appended once.
    """
    drop = set(gf_ids) | set(conflicts)
    kept = [r for r in (refs or []) if r.get("id") not in drop]
    kept.append({"id": selected_id})
    return kept


def apply_assignment(
    user: dict, gf_ids: set[str], selected_id: str, assign_field: str = "driver", groups_by_id: dict[str, Group] | None = None
) -> dict:
    """
    Return a shallow copy of `user` with the GF selection applied.

    With `groups_by_id` (the full group tree) the selected group's ancestors —
    typically the company group or the "General Foremen" parent — and
    descendants are removed from the field being set, which is what the
    MyGeotab UI does when you pick a sub-group. For assign_field "company" /
    "both" this narrows the user's data access to the GF group; "driver" (the
    default) leaves companyGroups untouched.
    """
    if selected_id not in gf_ids:
        raise ValueError("selected group is not a General Foreman group")
    fields = {"driver": ["driverGroups"], "company": ["companyGroups"], "both": ["driverGroups", "companyGroups"]}.get(assign_field)
    if not fields:
        raise ValueError(f"unknown ASSIGN_FIELD {assign_field!r}")
    conflicts: set[str] = set()
    if groups_by_id:
        conflicts = ancestors(groups_by_id, selected_id) | descendants(groups_by_id, selected_id)
    out = dict(user)
    for f in fields:
        out[f] = replace_gf_membership(user.get(f), gf_ids, selected_id, conflicts)
    return out


# --------------------------------------------------------------------------
# Pre-trip roll-up (emailer)
# --------------------------------------------------------------------------
def display_name(user: dict) -> str:
    first = (user.get("firstName") or "").strip()
    last = (user.get("lastName") or "").strip()
    full = f"{first} {last}".strip()
    return full or user.get("name") or user.get("id") or "?"


def is_pretrip(log: dict) -> bool:
    return str(log.get("logType") or "").replace("-", "").replace(" ", "").lower() == "pretrip"


def defect_count(log: dict) -> int:
    defects = log.get("dvirDefects")
    if defects is None:
        defects = log.get("defects")
    return len(defects or [])


def rollup_pretrips(
    logs: Iterable[dict],
    users_by_id: dict[str, dict],
    devices_by_id: dict[str, dict],
    gf_by_id: dict[str, dict],
    field: str = "driverGroups",
    to_local: callable = None,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """
    Group completed pre-trips by the driver's GF group.

    Returns (by_gf, unassigned):
      by_gf      { gf_group_id: [row, ...] }  rows sorted by driver name then time
      unassigned [row, ...]                   drivers with a pre-trip but no GF group
    row = {driver, driverId, vehicle, time (datetime or None), defects, safe}
    """
    by_gf: dict[str, list[dict]] = defaultdict(list)
    unassigned: list[dict] = []
    for log in logs:
        if not is_pretrip(log):
            continue
        driver_ref = log.get("driver") or {}
        driver_id = driver_ref.get("id") if isinstance(driver_ref, dict) else None
        user = users_by_id.get(driver_id or "", {"id": driver_id or "", "name": "Unknown driver"})
        device_ref = log.get("device") or {}
        device_id = device_ref.get("id") if isinstance(device_ref, dict) else None
        device = devices_by_id.get(device_id or "", {})
        when = log.get("dateTime")
        if isinstance(when, str):
            try:
                when = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except ValueError:
                when = None
        if when is not None and to_local:
            when = to_local(when)
        row = {
            "driver": display_name(user),
            "driverId": user.get("id") or driver_id or "",
            "vehicle": device.get("name") or (device_id or "—"),
            "time": when,
            "defects": defect_count(log),
            "safe": log.get("isSafeToOperate"),
        }
        gfs = current_gf(user, gf_by_id, field)
        if gfs:
            for gf in gfs:
                by_gf[gf["id"]].append(row)
        else:
            unassigned.append(row)

    def _key(r: dict):
        t = r["time"]
        return (r["driver"].lower(), t is None, t.timestamp() if t is not None else 0.0)

    for rows in by_gf.values():
        rows.sort(key=_key)
    unassigned.sort(key=_key)
    return dict(by_gf), unassigned
