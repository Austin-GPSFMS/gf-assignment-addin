from datetime import datetime, timezone

import gf_logic as L

GROUPS = [
    {"id": "GroupCompanyId", "name": "Company Group", "children": [{"id": "g_idaho"}, {"id": "g_gf"}]},
    {"id": "g_idaho", "name": "Idaho division", "children": [{"id": "g_idaho_private"}]},
    {"id": "g_idaho_private", "name": "Idaho Private Assets", "children": []},
    {"id": "g_gf", "name": "General Foremen", "children": [{"id": "g_whalen"}, {"id": "g_burke"}, {"id": "g_haggard"}]},
    {"id": "g_whalen", "name": "Bill Whalen", "children": [{"id": "g_whalen_crew2"}]},
    {"id": "g_whalen_crew2", "name": "Whalen Crew 2", "children": []},
    {"id": "g_burke", "name": "Mike Burke", "children": []},
    {"id": "g_haggard", "name": "Dan Haggard", "children": []},
]


def test_build_gf_list_by_name_direct_children_sorted():
    gf = L.build_gf_list(GROUPS, ["General Foremen"])
    assert [g["name"] for g in gf] == ["Bill Whalen", "Dan Haggard", "Mike Burke"]
    assert all(g["parent"] == "General Foremen" for g in gf)


def test_build_gf_list_by_id_and_descendants():
    gf = L.build_gf_list(GROUPS, ["g_gf"], include_descendants=True)
    assert "Whalen Crew 2" in [g["name"] for g in gf]


def test_build_gf_list_missing_parent_is_empty():
    assert L.build_gf_list(GROUPS, ["Nope"]) == []


def test_build_gf_list_multiple_parents_dedup():
    gf = L.build_gf_list(GROUPS, ["General Foremen", "g_gf", "Idaho division"])
    names = [g["name"] for g in gf]
    assert names.count("Bill Whalen") == 1
    assert "Idaho Private Assets" in names


def test_replace_membership_removes_other_gf_and_keeps_rest():
    refs = [{"id": "GroupCompanyId"}, {"id": "g_burke"}, {"id": "g_idaho"}]
    out = L.replace_gf_membership(refs, {"g_whalen", "g_burke", "g_haggard"}, "g_whalen")
    assert out == [{"id": "GroupCompanyId"}, {"id": "g_idaho"}, {"id": "g_whalen"}]


def test_replace_membership_from_none():
    assert L.replace_gf_membership(None, {"g_whalen"}, "g_whalen") == [{"id": "g_whalen"}]


def test_apply_assignment_driver_only_leaves_company_groups():
    user = {"id": "u1", "companyGroups": [{"id": "GroupCompanyId"}], "driverGroups": [{"id": "g_burke"}]}
    out = L.apply_assignment(user, {"g_whalen", "g_burke"}, "g_whalen", "driver")
    assert out["driverGroups"] == [{"id": "g_whalen"}]
    assert out["companyGroups"] == [{"id": "GroupCompanyId"}]
    assert user["driverGroups"] == [{"id": "g_burke"}]  # input untouched


def test_apply_assignment_both():
    user = {"id": "u1", "companyGroups": [{"id": "GroupCompanyId"}], "driverGroups": []}
    out = L.apply_assignment(user, {"g_whalen"}, "g_whalen", "both")
    assert out["driverGroups"] == [{"id": "g_whalen"}]
    assert out["companyGroups"] == [{"id": "GroupCompanyId"}, {"id": "g_whalen"}]


def test_apply_assignment_rejects_non_gf():
    try:
        L.apply_assignment({"id": "u1"}, {"g_whalen"}, "g_idaho", "driver")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_current_gf():
    gf_by_id = {"g_whalen": {"id": "g_whalen", "name": "Bill Whalen"}}
    user = {"driverGroups": [{"id": "GroupCompanyId"}, {"id": "g_whalen"}]}
    assert [g["name"] for g in L.current_gf(user, gf_by_id)] == ["Bill Whalen"]
    assert L.current_gf({"driverGroups": []}, gf_by_id) == []


def test_is_pretrip_variants():
    assert L.is_pretrip({"logType": "PreTrip"})
    assert L.is_pretrip({"logType": "pre-trip"})
    assert not L.is_pretrip({"logType": "PostTrip"})
    assert not L.is_pretrip({})


def test_rollup_pretrips_groups_and_unassigned():
    gf_by_id = {"g_whalen": {"id": "g_whalen", "name": "Bill Whalen"}, "g_burke": {"id": "g_burke", "name": "Mike Burke"}}
    users = {
        "u1": {"id": "u1", "firstName": "Ryan", "lastName": "Bingham", "driverGroups": [{"id": "g_whalen"}]},
        "u2": {"id": "u2", "firstName": "Eben", "lastName": "Boehm", "driverGroups": [{"id": "GroupCompanyId"}]},
        "u3": {"id": "u3", "firstName": "Kolby", "lastName": "Brown", "driverGroups": [{"id": "g_burke"}]},
    }
    devices = {"d1": {"id": "d1", "name": "T-101"}, "d2": {"id": "d2", "name": "T-202"}}
    logs = [
        {"logType": "PreTrip", "driver": {"id": "u3"}, "device": {"id": "d2"}, "dateTime": "2026-10-27T13:05:00.000Z", "dvirDefects": [{}]},
        {"logType": "PreTrip", "driver": {"id": "u1"}, "device": {"id": "d1"}, "dateTime": "2026-10-27T12:00:00.000Z", "dvirDefects": [], "isSafeToOperate": True},
        {"logType": "PostTrip", "driver": {"id": "u1"}, "device": {"id": "d1"}, "dateTime": "2026-10-27T23:00:00.000Z"},
        {"logType": "PreTrip", "driver": {"id": "u2"}, "device": {"id": "d1"}, "dateTime": "2026-10-27T12:30:00.000Z", "defects": [{}, {}]},
        {"logType": "PreTrip", "driver": {"id": "ghost"}, "device": {"id": "dX"}, "dateTime": "bad"},
    ]
    by_gf, unassigned = L.rollup_pretrips(logs, users, devices, gf_by_id)
    assert set(by_gf) == {"g_whalen", "g_burke"}
    row = by_gf["g_whalen"][0]
    assert row["driver"] == "Ryan Bingham" and row["vehicle"] == "T-101" and row["defects"] == 0 and row["safe"] is True
    assert row["time"] == datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    assert by_gf["g_burke"][0]["defects"] == 1
    names = [r["driver"] for r in unassigned]
    assert names == ["Eben Boehm", "Unknown driver"]
    assert unassigned[0]["defects"] == 2
