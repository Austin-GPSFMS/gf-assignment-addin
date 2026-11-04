import json

import pytest

import tenants as T


def test_single_database_from_env(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "DATABASES_FILE", tmp_path / "none.json")
    monkeypatch.setenv("GEOTAB_USERNAME", "automations@gpsfms.com")
    monkeypatch.setenv("GEOTAB_PASSWORD", "pw")
    monkeypatch.setenv("GEOTAB_DATABASE", "ILB")
    monkeypatch.setenv("ALLOWED_DATABASES", "ilb")
    monkeypatch.setenv("FALLBACK_TO", "a@x.com, b@x.com")
    monkeypatch.setenv("ASSIGN_FIELD", "both")
    ts = T.load_tenants()
    assert list(ts) == ["ilb"]
    t = ts["ilb"]
    assert t.gf_parent_groups == ["General Foremen"] and t.fallback_to == ["a@x.com", "b@x.com"]
    assert t.assign_field == "both" and t.membership_field == "driverGroups" and t.email_enabled


def test_databases_json_overrides_and_env_defaults(monkeypatch, tmp_path):
    f = tmp_path / "databases.json"
    f.write_text(json.dumps({
        "_comment": "ignored",
        "gpsfms_demo": {"username": "u", "password": "p", "reportTz": "America/Denver", "subjectPrefix": "[DEMO]", "fallbackTo": "austin@gpsfms.com"},
        "ILB": {"username": "u", "password": "p", "emailEnabled": False, "assignField": "company", "recipientsFile": "recipients.ilb.json", "ccAll": ["cc@x.com"]},
    }))
    monkeypatch.setattr(T, "DATABASES_FILE", f)
    monkeypatch.delenv("GEOTAB_DATABASE", raising=False)
    monkeypatch.setenv("GF_PARENT_GROUPS", "General Foremen - ID, General Foremen - OR")
    monkeypatch.setenv("FALLBACK_TO", "default@x.com")
    ts = T.load_tenants()
    assert set(ts) == {"gpsfms_demo", "ilb"}
    demo, ilb = ts["gpsfms_demo"], ts["ilb"]
    assert demo.report_tz == "America/Denver" and demo.subject_prefix == "[DEMO]" and demo.fallback_to == ["austin@gpsfms.com"]
    assert demo.gf_parent_groups == ["General Foremen - ID", "General Foremen - OR"]  # env default applied
    assert ilb.email_enabled is False and ilb.membership_field == "companyGroups" and ilb.cc_all == ["cc@x.com"]
    assert ilb.fallback_to == ["default@x.com"]
    assert ilb.recipients_path().name == "recipients.ilb.json"


def test_recipients_path_fallback_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "HERE", tmp_path)
    t = T.Tenant(database="demo", username="u", password="p")
    assert t.recipients_path() == tmp_path / "recipients.json"
    (tmp_path / "recipients.demo.json").write_text("{}")
    assert t.recipients_path() == tmp_path / "recipients.demo.json"


def test_missing_credentials_raise(monkeypatch, tmp_path):
    f = tmp_path / "databases.json"
    f.write_text(json.dumps({"x": {"username": "u"}}))
    monkeypatch.setattr(T, "DATABASES_FILE", f)
    monkeypatch.delenv("GEOTAB_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        T.load_tenants()


def test_bad_assign_field_raises(monkeypatch, tmp_path):
    f = tmp_path / "databases.json"
    f.write_text(json.dumps({"x": {"username": "u", "password": "p", "assignField": "nope"}}))
    monkeypatch.setattr(T, "DATABASES_FILE", f)
    with pytest.raises(RuntimeError):
        T.load_tenants()
