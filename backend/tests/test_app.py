"""API tests with MyGeotab fully stubbed out (no network)."""

import os

os.environ.setdefault("AUDIT_LOG", os.devnull)

import pytest
from fastapi.testclient import TestClient
from mygeotab.exceptions import MyGeotabException

import app as appmod
from geotab_client import SessionInvalid
from tenants import Tenant

GROUPS = [
    {"id": "GroupCompanyId", "name": "Company Group", "children": [{"id": "g_gf"}]},
    {"id": "g_gf", "name": "General Foremen", "children": [{"id": "g_whalen"}, {"id": "g_burke"}]},
    {"id": "g_whalen", "name": "Bill Whalen", "children": []},
    {"id": "g_burke", "name": "Mike Burke", "children": []},
]


class FakeSvc:
    _database = "ilb"

    def __init__(self):
        self.users = {
            "u1": {"id": "u1", "name": "rbingham@ilb.com", "firstName": "Ryan", "lastName": "Bingham", "isDriver": True,
                   "companyGroups": [{"id": "GroupCompanyId"}], "driverGroups": [{"id": "g_burke"}]}
        }
        self.sets = []
        self.fail_set = False

    def get(self, type_name, **search):
        assert type_name == "Group"
        return GROUPS

    def get_user_by_id(self, uid):
        return self.users.get(uid)

    def set_user(self, user):
        if self.fail_set:
            raise MyGeotabException({"errors": [{"name": "ArgumentException", "message": "Driver groups must be within the user's data access"}]})
        self.sets.append(user)
        self.users[user["id"]] = user


SESSION = {"server": "my123.geotab.com", "database": "ilb", "userName": "rbingham@ilb.com", "sessionId": "abcdefghijkl"}


@pytest.fixture
def client(monkeypatch):
    fake = FakeSvc()
    tenant = Tenant(database="ilb", username="automations@gpsfms.com", password="x", label="ILB", gf_parent_groups=["General Foremen"])
    monkeypatch.setattr(appmod, "_runtimes", {"ilb": appmod.Runtime(tenant, svc=fake)})
    monkeypatch.setattr(appmod, "validate_driver_session", lambda server, db, user, sid: {"id": "u1", "name": user})
    c = TestClient(appmod.app)
    c.fake = fake
    return c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["databases"]["ilb"]["gfGroups"] == 2


def test_me(client):
    r = client.post("/api/me", json=SESSION)
    assert r.status_code == 200
    j = r.json()
    assert j["displayName"] == "Ryan Bingham" and j["isDriver"] is True
    assert [g["name"] for g in j["current"]] == ["Mike Burke"]


def test_gf_groups(client):
    r = client.post("/api/gf-groups", json=SESSION)
    assert [g["name"] for g in r.json()["groups"]] == ["Bill Whalen", "Mike Burke"]


def test_assign_switches_gf(client):
    r = client.post("/api/assign", json={**SESSION, "groupId": "g_whalen"})
    assert r.status_code == 200, r.text
    assert [g["name"] for g in r.json()["current"]] == ["Bill Whalen"]
    sent = client.fake.sets[-1]
    assert sent["driverGroups"] == [{"id": "g_whalen"}]
    assert sent["companyGroups"] == [{"id": "GroupCompanyId"}]


def test_assign_same_is_noop(client):
    r = client.post("/api/assign", json={**SESSION, "groupId": "g_burke"})
    assert r.status_code == 200 and r.json()["message"] == "Already assigned." and client.fake.sets == []


def test_assign_rejects_non_gf_group(client):
    r = client.post("/api/assign", json={**SESSION, "groupId": "GroupCompanyId"})
    assert r.status_code == 400 and client.fake.sets == []


def test_assign_surfaces_geotab_error(client):
    client.fake.fail_set = True
    r = client.post("/api/assign", json={**SESSION, "groupId": "g_whalen"})
    assert r.status_code == 422 and "data access" in r.json()["detail"]


def test_bad_session(client, monkeypatch):
    def boom(*a):
        raise SessionInvalid("nope")

    monkeypatch.setattr(appmod, "validate_driver_session", boom)
    r = client.post("/api/me", json=SESSION)
    assert r.status_code == 401


def test_wrong_database(client):
    r = client.post("/api/me", json={**SESSION, "database": "wastepro"})
    assert r.status_code == 403


def test_database_match_is_case_insensitive(client):
    r = client.post("/api/me", json={**SESSION, "database": "ILB"})
    assert r.status_code == 200


def test_cors_allows_geotab_origin(client):
    r = client.options("/api/me", headers={"Origin": "https://my123.geotab.com", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "https://my123.geotab.com"
    r = client.options("/api/me", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") is None
