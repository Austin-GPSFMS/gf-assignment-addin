import os
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("SMTP_USER", "reports@gpsfms.com")

import pretrip_email as E


def test_render_and_build_message_has_logo_and_rows():
    tz = ZoneInfo("America/Los_Angeles")
    rows = [
        {"driver": "Ryan Bingham", "driverId": "u1", "vehicle": "T-101", "time": datetime(2026, 10, 27, 6, 5, tzinfo=tz), "defects": 0, "safe": True},
        {"driver": "Kolby <Brown>", "driverId": "u3", "vehicle": "T-202", "time": None, "defects": 2, "safe": False},
    ]
    html = E.render_email("Pre-trip — Bill Whalen", "Mon · 2 of 5", rows, "note", "__LOGO__")
    assert "Ryan Bingham" in html and "Kolby &lt;Brown&gt;" in html and "6:05 AM" in html
    msg = E.build_message("subj", ["a@x.com"], ["c@x.com"], html, "text")
    assert msg["To"] == "a@x.com" and msg["Cc"] == "c@x.com"
    parts = [p.get_content_type() for p in msg.walk()]
    assert "text/html" in parts and "image/png" in parts
    assert "cid:__LOGO__" not in msg.get_payload()[1].get_payload()[0].get_content()


def test_render_empty():
    html = E.render_email("t", "s", [], None, None)
    assert "No pre-trip inspections" in html


def test_resolve_recipients_precedence():
    users = {"u9": {"id": "u9", "name": "bwhalen@ilb.com", "firstName": "Bill", "lastName": "Whalen"}}
    assert E.resolve_recipients("bill whalen", users, {"bill whalen": ["x@y.com"]}) == (["x@y.com"], "recipients file")
    assert E.resolve_recipients("Bill Whalen", users, {}) == (["bwhalen@ilb.com"], "MyGeotab user")
    assert E.resolve_recipients("Nobody", users, {}, ["safety@ilb.com", "ops@ilb.com"]) == (["safety@ilb.com", "ops@ilb.com"], "fallbackTo")


def test_day_window_is_local_midnight_in_utc():
    frm, to = E.day_window(datetime(2026, 10, 27).date(), ZoneInfo("America/Los_Angeles"))
    assert frm.isoformat() == "2026-10-27T07:00:00+00:00" and (to - frm).days == 1


def test_run_tenant_dry_run_per_database(monkeypatch, tmp_path):
    """End-to-end dry run with MyGeotab stubbed: one file per GF + unassigned, prefixed by database."""
    from tenants import Tenant

    class FakeSvc:
        def __init__(self, *a, **k):
            pass

        def get(self, type_name, **search):
            return {
                "Group": [
                    {"id": "GroupCompanyId", "name": "Company Group", "children": [{"id": "g_gf"}]},
                    {"id": "g_gf", "name": "General Foremen", "children": [{"id": "g1"}]},
                    {"id": "g1", "name": "Bill Whalen", "children": []},
                ],
                "User": [
                    {"id": "u1", "name": "r@x.com", "firstName": "Ryan", "lastName": "B", "driverGroups": [{"id": "g1"}]},
                    {"id": "u2", "name": "e@x.com", "firstName": "Eben", "lastName": "B", "driverGroups": []},
                ],
                "Device": [{"id": "d1", "name": "T-1"}],
                "DVIRLog": [
                    {"logType": "PreTrip", "driver": {"id": "u1"}, "device": {"id": "d1"}, "dateTime": "2026-10-27T13:00:00Z", "dvirDefects": []},
                    {"logType": "PreTrip", "driver": {"id": "u2"}, "device": {"id": "d1"}, "dateTime": "2026-10-27T13:30:00Z", "dvirDefects": []},
                ],
            }[type_name]

    monkeypatch.setattr(E, "ServiceAccount", FakeSvc)
    t = Tenant(database="gpsfms_demo", username="u", password="p", fallback_to=["austin@gpsfms.com"], subject_prefix="[DEMO]")
    args = type("A", (), {"only": None, "skip_empty": False, "dry_run": True})()
    sent = E.run_tenant(t, datetime(2026, 10, 27).date(), args, tmp_path)
    assert sent == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["gpsfms_demo_2026-10-27_Bill_Whalen.html", "gpsfms_demo_2026-10-27_UNASSIGNED.html"]
    assert "Ryan B" in (tmp_path / names[0]).read_text() and "1 of 1 assigned" in (tmp_path / names[0]).read_text()
    assert "Eben B" in (tmp_path / names[1]).read_text()
