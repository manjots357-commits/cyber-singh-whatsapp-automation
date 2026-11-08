"""Integration tests: HTTP API, auth guard and webhook."""
import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import config
import database as db
from app import app

client = TestClient(app)


def sign(body: bytes, secret="test-app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_webhook(payload: dict, signature=True):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature:
        headers["X-Hub-Signature-256"] = sign(raw)
    return client.post("/webhook", content=raw, headers=headers)


def make_sendable(n=2, base=9300000000):
    return [db.add_contact(f"U{i}", f"+91{base + i}", "vip_clients", opted_in=True, consent_basis="signed_up_form")
            for i in range(n)]


# ------------------------------------------------------------------ basics
def test_config_masks_ids_and_exposes_no_secret(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "SUPERSECRET")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    body = client.get("/api/config").text
    assert "SUPERSECRET" not in body and "1234567890" not in body


def test_groups_and_readiness():
    assert len(client.get("/api/groups").json()) == 5
    r = client.get("/api/meta/readiness").json()
    assert r["safe_mode_active"] is True and r["ready_for_live"] is False
    assert any(c["item"] == "Access token" for c in r["checklist"])


# ------------------------------------------------------------------ auth guard
def test_dashboard_requires_password_when_set(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "pw123")
    assert client.get("/api/groups").status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"admin:pw123").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"admin:nope").decode()}
    assert client.get("/api/groups", headers=good).status_code == 200
    assert client.get("/api/groups", headers=bad).status_code == 401
    assert client.get("/health").status_code == 200       # public


def test_dashboard_refuses_public_host_without_password():
    r = client.get("/api/groups", headers={"Host": "abc123.ngrok.app"})
    assert r.status_code == 403
    assert client.get("/api/groups", headers={"Host": "localhost:8000"}).status_code == 200


# ------------------------------------------------------------------ consent API
def test_consent_requires_attestation_and_basis():
    cid = db.add_contact("X", "+919400000000", "vip_clients")
    assert client.post("/api/contacts/consent", json={"contact_ids": [cid], "basis": "signed_up_form"}).status_code == 400
    assert client.post("/api/contacts/consent",
                       json={"contact_ids": [cid], "basis": "made_up", "attest": True}).status_code == 400
    ok = client.post("/api/contacts/consent", json={"contact_ids": [cid], "basis": "signed_up_form", "attest": True})
    assert ok.status_code == 200 and ok.json()["updated"] == 1
    assert client.get("/api/consent/summary").json()["sendable"] == 1


def test_create_contact_opt_in_needs_basis():
    r = client.post("/api/contacts", json={"name": "A", "phone": "98765 43210", "group_id": "vip_clients",
                                           "opted_in": True})
    assert r.status_code == 400
    r = client.post("/api/contacts", json={"name": "A", "phone": "98765 43210", "group_id": "vip_clients",
                                           "opted_in": True, "consent_basis": "asked_to_receive"})
    assert r.status_code == 200
    assert client.post("/api/contacts", json={"name": "A", "phone": "12", "group_id": "vip_clients"}).status_code == 400


def test_import_endpoint_imports_as_unverified():
    csv_text = "Name,Phone\nGurpreet,98765 43210\nSimran,98123 45678\n"
    r = client.post("/api/contacts/import", json={"csv_text": csv_text, "group_id": "new_leads"}).json()
    assert r["imported"] == 2
    s = client.get("/api/consent/summary").json()
    assert s["real_total"] == 2 and s["sendable"] == 0 and s["unverified"] == 2


# ------------------------------------------------------------------ full campaign flow via API
def test_preview_approve_flow_simulation():
    make_sendable(2)
    db.upsert_template("vid_update", "pa", "MARKETING", "APPROVED", "Hello {{1}} watch {{2}} now please")
    p = client.post("/api/campaigns/preview", json={
        "title": "Ep 9", "video_url": "https://example.com/ep9", "target_groups": ["vip_clients"],
        "template_name": "vid_update", "template_language": "pa"}).json()
    assert p["mode"] == "SIMULATION" and p["ready_count"] == 2
    assert "https://example.com/ep9" in p["screened_recipients"][0]["personalized_msg"]
    a = client.post(f"/api/campaigns/{p['campaign_id']}/approve", json={"confirmed": True, "expected_count": 2})
    assert a.status_code == 200 and a.json()["status"] == "IN_PROGRESS"
    detail = client.get(f"/api/campaigns/{p['campaign_id']}").json()
    assert detail["campaign"]["status"] == "COMPLETED"         # TestClient runs the background task
    assert detail["progress"] == {"SIMULATED": 2}


def test_preview_rejects_bad_url_and_empty_audience():
    assert client.post("/api/campaigns/preview", json={"title": "x", "video_url": "ftp://x",
                                                       "target_groups": ["vip_clients"]}).status_code == 400
    assert client.post("/api/campaigns/preview", json={"title": "x", "video_url": "https://x.com"}).status_code == 400


def test_unconfirmed_approval_cancels_and_sends_nothing():
    make_sendable(1)
    p = client.post("/api/campaigns/preview", json={"title": "x", "video_url": "https://x.com/a",
                                                    "target_groups": ["vip_clients"]}).json()
    r = client.post(f"/api/campaigns/{p['campaign_id']}/approve", json={"confirmed": False}).json()
    assert r["status"] == "CANCELLED"
    assert client.get(f"/api/campaigns/{p['campaign_id']}").json()["dispatches"] == []


def test_live_approval_requires_expected_count(live_mode):
    make_sendable(1)
    db.upsert_template("vid_update", "pa", "MARKETING", "APPROVED", "Hello {{1}} watch {{2}} now please")
    p = client.post("/api/campaigns/preview", json={"title": "x", "video_url": "https://x.com/live",
                                                    "target_groups": ["vip_clients"],
                                                    "template_name": "vid_update"}).json()
    assert p["mode"] == "LIVE"
    assert client.post(f"/api/campaigns/{p['campaign_id']}/approve", json={"confirmed": True}).status_code == 400
    ok = client.post(f"/api/campaigns/{p['campaign_id']}/approve", json={"confirmed": True, "expected_count": 1})
    assert ok.status_code == 200 and len(live_mode.calls) == 1


def test_analytics_endpoint():
    make_sendable(1)
    p = client.post("/api/campaigns/preview", json={"title": "x", "video_url": "https://x.com/an",
                                                    "target_groups": ["vip_clients"]}).json()
    # free text without window -> nobody ready; analytics must still respond
    a = client.get("/api/analytics").json()
    assert "delivery_rate" in a and "consent" in a and a["unique_recipients_24h"] == 0


# ------------------------------------------------------------------ templates
def test_template_create_validation_and_meta_call(live_mode):
    assert client.post("/api/templates", json={"name": "Bad Name", "body": "Hello {{1}} text here ok"}).status_code == 400
    live_mode.responder = lambda url, j: __import__("conftest").FakeResponse(
        200, {"id": "555", "status": "PENDING", "category": "MARKETING"})
    r = client.post("/api/templates", json={"name": "my_tpl", "language": "pa", "body": "Hello {{1}} watch {{2}} now ok",
                                            "footer": "Reply STOP"})
    assert r.status_code == 200 and r.json()["status"] == "PENDING"
    sent = live_mode.calls[0]["json"]
    assert sent["name"] == "my_tpl" and sent["category"] == "MARKETING"
    assert sent["components"][0]["example"]["body_text"] and sent["components"][-1]["type"] == "BUTTONS"
    assert db.get_template("my_tpl")["status"] == "PENDING"


def test_test_send_only_to_owner_number(live_mode, monkeypatch):
    assert client.post("/api/test-send", json={"template_name": "t"}).status_code == 400
    monkeypatch.setattr(config, "OWNER_WHATSAPP_NUMBER", "919876543210")
    r = client.post("/api/test-send", json={"template_name": "t", "language": "pa", "params": ["a", "b"]})
    assert r.status_code == 200 and live_mode.calls[0]["json"]["to"] == "919876543210"


# ------------------------------------------------------------------ webhook
def test_webhook_verification_handshake():
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                        "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong",
                                          "hub.challenge": "1"}).status_code == 403


def test_webhook_rejects_unsigned_and_wrongly_signed():
    payload = {"object": "whatsapp_business_account", "entry": []}
    assert post_webhook(payload, signature=False).status_code == 403
    raw = json.dumps(payload).encode()
    r = client.post("/webhook", content=raw, headers={"X-Hub-Signature-256": sign(raw, "other-secret")})
    assert r.status_code == 403
    assert post_webhook(payload).status_code == 200


def status_payload(wa_id, status, **extra):
    st = {"id": wa_id, "status": status, "timestamp": "1760000000", "recipient_id": "919300000000", **extra}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": {"statuses": [st]}}]}]}


def message_payload(sender, text, mtype="text"):
    msg = {"from": sender, "id": "wamid.IN", "type": mtype}
    msg["text" if mtype == "text" else "button"] = {"body": text} if mtype == "text" else {"text": text, "payload": text}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": {"messages": [msg]}}]}]}


def seed_sent(wa_id="wamid.X1"):
    ids = make_sendable(1)
    cid = db.create_campaign("t", "https://x.com/w", "m", ["vip_clients"])
    db.record_dispatch(cid, ids[0], "+919300000000", "https://x.com/w", "m", "SENT", wa_id=wa_id)
    return cid


def test_delivery_receipts_update_and_never_go_backwards():
    cid = seed_sent()
    for st in ("delivered", "read", "delivered", "sent"):
        assert post_webhook(status_payload("wamid.X1", st)).status_code == 200
    d = db.get_campaign_details(cid)["dispatches"][0]
    assert d["status"] == "READ" and d["delivered_at"] and d["read_at"]


def test_failed_status_stores_error():
    cid = seed_sent()
    post_webhook(status_payload("wamid.X1", "failed", errors=[{"code": 131049, "title": "Not delivered by Meta"}]))
    d = db.get_campaign_details(cid)["dispatches"][0]
    assert d["status"] == "FAILED" and d["error_code"] == "131049"
    assert db.analytics()["top_errors"][0]["code"] == "131049"


def test_stop_keyword_opts_out_and_start_opts_in():
    ids = make_sendable(1)
    for word in ("STOP", " stop! ", "Unsubscribe"):
        db.apply_inbound_opt_in("919300000000")
        post_webhook(message_payload("919300000000", word))
        assert not db.is_sendable(db.get_contacts_by_ids(ids)[0]), word
    post_webhook(message_payload("919300000000", "START"))
    assert db.is_sendable(db.get_contacts_by_ids(ids)[0])


def test_quick_reply_button_stop_opts_out():
    ids = make_sendable(1)
    post_webhook(message_payload("919300000000", "STOP", mtype="button"))
    assert not db.is_sendable(db.get_contacts_by_ids(ids)[0])


def test_inbound_message_opens_service_window_and_unknown_sender_is_safe():
    ids = make_sendable(1)
    post_webhook(message_payload("919300000000", "hello"))
    assert db.within_service_window(db.get_contacts_by_ids(ids)[0])
    assert post_webhook(message_payload("919999999999", "STOP")).status_code == 200


def test_template_status_update_event():
    db.upsert_template("vid_update", "pa", "MARKETING", "PENDING", "Hello {{1}} watch {{2}} now")
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{
        "field": "message_template_status_update",
        "value": {"event": "APPROVED", "message_template_name": "vid_update", "message_template_language": "pa"}}]}]}
    post_webhook(payload)
    assert db.get_template("vid_update", "pa")["status"] == "APPROVED"
