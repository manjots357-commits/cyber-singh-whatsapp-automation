"""Unit tests: phone handling, consent model, import, Cloud API client, campaign engine."""
import json

import pytest

import campaign_service as cs
import config
import contact_import
import database as db
import whatsapp_service as wa
from conftest import FakeResponse
from phone_utils import normalize_phone


# ------------------------------------------------------------------ phone numbers
@pytest.mark.parametrize("raw,expected", [
    ("98765 43210", "+919876543210"),
    ("+91 98765-43210", "+919876543210"),
    ("919876543210", "+919876543210"),
    ("09876543210", "+919876543210"),
    ("0091 9876543210", "+919876543210"),
    ("+447911123456", "+447911123456"),
    ("98765 43210 ::: 12345", "+919876543210"),
    ("12345", None), ("", None), ("abc", None), ("+911234567890", None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


# ------------------------------------------------------------------ consent model
def _contact(name="A", phone="+919876543210", group="vip_clients", **kw):
    return db.add_contact(name, phone, group, **kw)


def test_groups_exist():
    assert len(db.get_all_groups()) == 5


def test_new_contact_is_not_sendable_by_default():
    cid = _contact()
    c = db.get_contacts_by_ids([cid])[0]
    assert c["opted_in"] == 0 and not db.is_sendable(c)


def test_opted_in_without_basis_is_not_verified():
    cid = _contact(opted_in=True)  # no consent_basis given
    assert not db.is_sendable(db.get_contacts_by_ids([cid])[0])


def test_set_consent_makes_sendable_and_logs():
    cid = _contact()
    assert db.set_consent([cid], "asked_to_receive") == 1
    c = db.get_contacts_by_ids([cid])[0]
    assert db.is_sendable(c) and c["consent_basis"] == "asked_to_receive"
    conn = db.get_db_connection()
    assert conn.execute("SELECT COUNT(*) FROM consent_log WHERE contact_id=?", (cid,)).fetchone()[0] == 1


def test_owner_cannot_override_an_opt_out():
    cid = _contact(opted_in=True, consent_basis="signed_up_form")
    db.revoke_consent([cid], "inbound_stop_keyword")
    assert db.set_consent([cid], "asked_to_receive") == 0           # blocked
    _contact(opted_in=True, consent_basis="signed_up_form")          # re-adding via form does not undo it
    assert not db.is_sendable(db.get_contacts_by_ids([cid])[0])


def test_contact_can_opt_back_in_themselves():
    cid = _contact(opted_in=True, consent_basis="signed_up_form")
    db.apply_inbound_opt_out("919876543210")
    assert db.apply_inbound_opt_in("919876543210")
    assert db.is_sendable(db.get_contacts_by_ids([cid])[0])


# ------------------------------------------------------------------ import
GOOGLE_CSV = (
    "First Name,Last Name,Organization Name,E-mail 1 - Value,Phone 1 - Value,Phone 2 - Value\n"
    "Gurpreet,Singh,,g@x.com,98765 43210 ::: 99999 88888,\n"
    "Simran,Kaur,,,,98123 45678\n"
    ",,Acme Shop,,+447911123456,\n"
    "NoPhone,Person,,,,\n"
    "Dup,Licate,,,9876543210,\n"
)


def test_import_never_grants_consent():
    stats = contact_import.import_csv_text(GOOGLE_CSV)
    assert stats["imported"] == 3 and stats["invalid"] == 1 and stats["duplicate_in_file"] == 1
    contacts = db.get_contacts(only_opted_in=False, is_test=False)
    assert len(contacts) == 3
    assert all(c["opted_in"] == 0 and c["consent_verified"] == 0 for c in contacts)
    assert not any(db.is_sendable(c) for c in contacts)


def test_import_preserves_existing_consent_and_group():
    cid = _contact("Gurpreet", "+919876543210", "vip_clients", opted_in=True, consent_basis="signed_up_form")
    contact_import.import_csv_text(GOOGLE_CSV, group_id="new_leads")
    c = db.get_contacts_by_ids([cid])[0]
    assert c["group_id"] == "vip_clients" and db.is_sendable(c)


# ------------------------------------------------------------------ Cloud API client
def test_template_payload_and_param_cleaning(live_mode):
    r = wa.send_template_message("+91 98765 43210", "cyber_singh_video_update", "pa",
                                 ["Gur\npreet", "https://example.com/a  b"])
    assert r["success"] and r["wa_id"].startswith("wamid.")
    call = live_mode.calls[0]
    assert call["url"].endswith("/1234567890/messages") and f"/{config.META_GRAPH_VERSION}/" in call["url"]
    assert call["headers"]["Authorization"] == "Bearer TEST_TOKEN"
    t = call["json"]
    assert t["to"] == "919876543210" and t["type"] == "template"
    assert t["template"]["name"] == "cyber_singh_video_update" and t["template"]["language"]["code"] == "pa"
    params = t["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["Gur preet", "https://example.com/a b"]


def test_safe_mode_never_calls_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used in safe mode")
    monkeypatch.setattr(wa.requests, "post", boom)
    r = wa.send_template_message("+919876543210", "t", "pa", ["x", "y"])
    assert r["success"] and r["simulated"] and r["wa_id"].startswith("SIM_MSG_")


def test_error_classification(live_mode):
    assert wa.classify_error("190") == "fatal"
    assert wa.classify_error("130429") == "retry"
    assert wa.classify_error("131026") == "permanent"
    assert wa.classify_error("999999") == "permanent"
    assert wa.classify_error(None, 503) == "retry"


def test_retry_then_success(live_mode):
    seq = [FakeResponse(429, {"error": {"code": 130429, "message": "rate"}}),
           FakeResponse(200, {"messages": [{"id": "wamid.OK"}]})]
    live_mode.responder = lambda url, j: seq.pop(0)
    r = wa.send_template_message("+919876543210", "t", "pa", ["a", "b"])
    assert r["success"] and r["wa_id"] == "wamid.OK" and len(live_mode.calls) == 2


def test_template_input_validation():
    assert wa.validate_template_input("good_name", "Hello {{1}} watch {{2}} today please") == []
    assert wa.validate_template_input("Bad Name", "Hello {{1}} watch {{2}} today")
    assert wa.validate_template_input("x", "{{1}} starts with variable here")
    assert wa.validate_template_input("x", "Hello {{1}} and {{3}} skipped number")


# ------------------------------------------------------------------ campaign engine
def _sendable(n=3, group="vip_clients", base=9100000000):
    ids = []
    for i in range(n):
        cid = db.add_contact(f"User{i}", f"+91{base + i}", group, opted_in=True, consent_basis="signed_up_form")
        ids.append(cid)
    return ids


def _approved_template(name="vid_update", lang="pa", body="Hello {{1}} watch {{2}} now please"):
    db.upsert_template(name, lang, "MARKETING", "APPROVED", body)


def _campaign(**kw):
    args = dict(title="Vid", video_url="https://youtu.be/abc", message_template="Hi {{name}} {{video_url}}",
                target_groups=["vip_clients"], template_name="vid_update", template_language="pa")
    args.update(kw)
    cid = db.create_campaign(**args)
    return cid


def test_simulation_end_to_end_and_dedupe():
    _sendable(3); _approved_template()
    cid = _campaign()
    s = cs.start_campaign(cid)
    assert s["ready"] == 3
    out = cs.run_campaign(cid)
    assert out["status"] == "COMPLETED" and out["sent"] == 3
    statuses = {d["status"] for d in db.get_campaign_details(cid)["dispatches"]}
    assert statuses == {"SIMULATED"}
    # a simulation must NOT block the real send later
    assert db.check_prior_video_send("+919100000000", "https://youtu.be/abc") is False


def test_live_send_records_wa_ids_and_blocks_duplicates(live_mode):
    _sendable(2); _approved_template()
    cid = _campaign()
    cs.start_campaign(cid, expected_count=2)
    out = cs.run_campaign(cid)
    assert out["status"] == "COMPLETED" and out["sent"] == 2
    assert len(live_mode.calls) == 2
    d = db.get_campaign_details(cid)["dispatches"]
    assert {x["status"] for x in d} == {"SENT"} and all(x["wa_message_id"].startswith("wamid.") for x in d)
    # same URL again -> everyone is a duplicate -> nothing to send
    cid2 = _campaign()
    with pytest.raises(cs.CampaignError):
        cs.start_campaign(cid2)
    assert len(live_mode.calls) == 2


def test_unconsented_unverified_and_opted_out_are_never_messaged(live_mode):
    ids = _sendable(1)
    db.add_contact("NoConsent", "+919200000001", "vip_clients")
    db.add_contact("Imported", "+919200000002", "vip_clients", opted_in=True)           # no basis
    oo = db.add_contact("OptedOut", "+919200000003", "vip_clients", opted_in=True, consent_basis="signed_up_form")
    db.revoke_consent([oo], "inbound_stop_keyword")
    _approved_template()
    cid = _campaign()
    screen = cs.screen_campaign(db.get_campaign(cid))
    assert len(screen["ready"]) == 1 and screen["ready"][0]["contact"]["id"] == ids[0]
    cs.start_campaign(cid, expected_count=1); cs.run_campaign(cid)
    assert [c["json"]["to"] for c in live_mode.calls] == ["919100000000"]


def test_test_contacts_and_real_contacts_never_mix(live_mode):
    _sendable(1)
    db.seed_isolated_dummy_contacts()
    _approved_template()
    real = cs.screen_campaign(db.get_campaign(_campaign()))
    test = cs.screen_campaign(db.get_campaign(_campaign(is_test_run=True)))
    assert all(not i["contact"]["is_test"] for i in real["ready"])
    assert test["ready"] and all(i["contact"]["is_test"] for i in test["ready"])
    cid = _campaign(is_test_run=True)
    cs.start_campaign(cid); cs.run_campaign(cid)
    assert live_mode.calls == []        # test campaigns never reach Meta, even in live mode


def test_audience_change_between_preview_and_approval_is_rejected():
    ids = _sendable(3); _approved_template()
    cid = _campaign()
    db.revoke_consent([ids[0]], "inbound_stop_keyword")
    with pytest.raises(cs.CampaignError) as e:
        cs.start_campaign(cid, expected_count=3)
    assert e.value.status_code == 409


def test_consent_is_rechecked_right_before_each_send(live_mode, monkeypatch):
    ids = _sendable(3); _approved_template()
    cid = _campaign()
    cs.start_campaign(cid, expected_count=3)
    real_send = cs._send_one
    state = {"n": 0}

    def send_and_stop_second(camp, d, contact):
        state["n"] += 1
        if state["n"] == 1:
            db.apply_inbound_opt_out("919100000001")    # second contact replies STOP mid-run
        return real_send(camp, d, contact)
    monkeypatch.setattr(cs, "_send_one", send_and_stop_second)
    out = cs.run_campaign(cid)
    assert len(live_mode.calls) == 2 and out["sent"] == 2
    skipped = [d for d in db.get_campaign_details(cid)["dispatches"] if d["status"] == "SKIPPED_NO_CONSENT"]
    assert len(skipped) == 1


def test_permanent_error_continues_fatal_error_pauses(live_mode):
    _sendable(4); _approved_template()
    cid = _campaign()
    cs.start_campaign(cid, expected_count=4)
    seq = [FakeResponse(400, {"error": {"code": 131026, "message": "undeliverable"}}),
           FakeResponse(200, {"messages": [{"id": "wamid.A"}]}),
           FakeResponse(401, {"error": {"code": 190, "message": "token expired"}})]
    live_mode.responder = lambda url, j: seq.pop(0)
    out = cs.run_campaign(cid)
    # contact1: permanent failure; contact2: sent; contact3: fatal (token) -> stays pending; contact4 untouched
    assert out["status"] == "PAUSED" and out["sent"] == 1 and out["failed"] == 1 and out["remaining"] == 2
    assert "token" in db.get_campaign(cid)["status_note"].lower()
    # fix the problem, resume: only the 2 unsent contacts are sent; the permanent failure is not retried
    live_mode.responder = lambda url, j: FakeResponse(200, {"messages": [{"id": "wamid.B%d" % len(live_mode.calls)}]})
    cs.start_campaign(cid, resume=True)
    out2 = cs.run_campaign(cid)
    assert out2["status"] == "COMPLETED" and out2["remaining"] == 0 and out2["sent"] == 3 and out2["failed"] == 1
    assert len(live_mode.calls) == 5
    to = [c["json"]["to"] for c in live_mode.calls]
    assert len(set(to[3:])) == 2                      # two different people in the resumed run


def test_circuit_breaker_after_unexplained_failures(live_mode, monkeypatch):
    monkeypatch.setattr(config, "FAILURE_CIRCUIT_BREAKER", 3)
    _sendable(6); _approved_template()
    cid = _campaign()
    cs.start_campaign(cid, expected_count=6)
    live_mode.responder = lambda url, j: FakeResponse(400, {"error": {"code": 777777, "message": "weird"}})
    out = cs.run_campaign(cid)
    assert out["status"] == "PAUSED" and len(live_mode.calls) == 3


def test_daily_capacity_defers_and_resume_continues(live_mode, monkeypatch):
    monkeypatch.setattr(config, "DAILY_RECIPIENT_LIMIT", 2)
    _sendable(5); _approved_template()
    cid = _campaign()
    screen = cs.screen_campaign(db.get_campaign(cid))
    assert len(screen["ready"]) == 2 and len(screen["deferred"]) == 3
    cs.start_campaign(cid, expected_count=2)
    out = cs.run_campaign(cid)
    assert out["status"] == "PARTIAL" and out["remaining"] == 3 and len(live_mode.calls) == 2
    with pytest.raises(cs.CampaignError):                     # still no capacity today
        cs.start_campaign(cid, resume=True)


def test_free_text_only_inside_24h_window(live_mode):
    ids = _sendable(2)
    db.touch_inbound("919100000000")                          # first contact wrote to us just now
    cid = _campaign(template_name=None, template_language=None)
    screen = cs.screen_campaign(db.get_campaign(cid))
    assert len(screen["ready"]) == 1 and len(screen["needs_template"]) == 1
    cs.start_campaign(cid, expected_count=1); cs.run_campaign(cid)
    assert live_mode.calls[0]["json"]["type"] == "text"


def test_live_requires_approved_template(live_mode):
    _sendable(1)
    cid = _campaign()
    with pytest.raises(cs.CampaignError) as e:
        cs.start_campaign(cid)
    assert "sync" in e.value.message.lower()
    db.upsert_template("vid_update", "pa", "MARKETING", "PENDING", "Hello {{1}} watch {{2}} now please")
    with pytest.raises(cs.CampaignError) as e:
        cs.start_campaign(cid)
    assert "PENDING" in e.value.message
    db.upsert_template("vid_update", "pa", "MARKETING", "APPROVED", "Hello {{1}} only one var please")
    with pytest.raises(cs.CampaignError) as e:
        cs.start_campaign(cid)
    assert "variable" in e.value.message


def test_cancel_stops_running_campaign():
    _sendable(3); _approved_template()
    cid = _campaign()
    cs.start_campaign(cid)
    assert cs.cancel_campaign(cid) == "CANCELLING"
    out = cs.run_campaign(cid)
    assert out["status"] == "CANCELLED"


def test_interrupted_campaign_is_recovered():
    cid = _campaign()
    db.set_campaign_fields(cid, status="IN_PROGRESS")
    assert db.recover_interrupted_campaigns() == 1
    assert db.get_campaign(cid)["status"] == "INTERRUPTED"


def test_historic_simulated_rows_are_relabelled():
    cid = _campaign()
    c = _sendable(1)[0]
    conn = db.get_db_connection()
    conn.execute("""INSERT INTO dispatches (campaign_id, contact_id, phone, video_url, message_content, status, wa_message_id)
                    VALUES (?,?,?,?,?,?,?)""", (cid, c, "+919100000000", "https://old", "m", "SENT", "SIM_MSG_1_0000"))
    conn.commit(); conn.close()
    db.init_db()
    assert db.check_prior_video_send("+919100000000", "https://old") is False
