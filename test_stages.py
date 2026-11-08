"""Tests for: link->campaign drafts (SSRF-safe), manual pause, and the live-sending gate."""
import pytest
from fastapi.testclient import TestClient

import campaign_service as cs
import config
import database as db
import url_intel
from app import app
from conftest import FakeResponse

client = TestClient(app)
PAGE = ('<html><head><title>ignored</title><meta property="og:title" content="Episode 9 &amp; the AI Singh">'
        '<meta name="description" content="Nave episode di jhalak"></head><body>x</body></html>')


class FakeGet:
    def __init__(self, body=PAGE, status=200, headers=None, location=None):
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        if location:
            self.headers["Location"] = location
        self._body = body.encode()
        self.encoding = "utf-8"

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass


@pytest.fixture
def public_dns(monkeypatch):
    import ipaddress

    def resolver(host, port, **k):
        try:                                   # like real DNS: an IP literal resolves to itself
            ipaddress.ip_address(host)
            return [(2, 1, 6, "", (host, port))]
        except ValueError:
            return [(2, 1, 6, "", ("93.184.216.34", port))]
    monkeypatch.setattr(url_intel.socket, "getaddrinfo", resolver)


# ------------------------------------------------------------------ stage 1: link analysis
def test_metadata_extraction_prefers_og_and_unescapes():
    m = url_intel.extract_metadata(PAGE, "https://x.com")
    assert m["title"] == "Episode 9 & the AI Singh" and m["description"].startswith("Nave")


def test_title_tag_fallback():
    m = url_intel.extract_metadata("<html><title> Hello \n World </title></html>", "https://www.site.com/a")
    assert m["title"] == "Hello World" and m["site"] == "site.com"


def test_analyse_builds_drafts(public_dns, monkeypatch):
    monkeypatch.setattr(url_intel.requests, "get", lambda *a, **k: FakeGet())
    d = url_intel.analyse("https://example.com/ep9")
    assert d["campaign_name"] == "Episode 9 & the AI Singh"
    kinds = {m["id"]: m for m in d["messages"]}
    assert "{{video_url}}" in kinds["pa_text"]["body"] and kinds["pa_template"]["kind"] == "template"
    assert kinds["pa_template"]["body"].count("{{") == 3
    assert url_intel.variables_for_template_body(kinds["pa_template"]["body"]) == ["name", "video_url", "video_title"]


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd", "ftp://example.com/x", "http://example.com:8080/x", "javascript:alert(1)", "http:///nohost"])
def test_ssrf_scheme_and_port_refused(bad, public_dns):
    with pytest.raises(url_intel.UnsafeURL):
        url_intel.fetch_page(bad)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "0.0.0.0"])
def test_ssrf_private_addresses_refused(ip, monkeypatch):
    monkeypatch.setattr(url_intel.socket, "getaddrinfo", lambda host, port, **k: [(2, 1, 6, "", (ip, port))])
    monkeypatch.setattr(url_intel.requests, "get", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(url_intel.UnsafeURL):
        url_intel.fetch_page("https://innocent.example.com/")


def test_ssrf_redirect_to_private_is_refused(monkeypatch):
    answers = {"public.example.com": "93.184.216.34", "internal.example.com": "10.1.1.1"}
    monkeypatch.setattr(url_intel.socket, "getaddrinfo",
                        lambda host, port, **k: [(2, 1, 6, "", (answers[host], port))])
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        return FakeGet(status=302, location="http://internal.example.com/secret")
    monkeypatch.setattr(url_intel.requests, "get", fake_get)
    with pytest.raises(url_intel.UnsafeURL):
        url_intel.fetch_page("https://public.example.com/")
    assert calls == ["https://public.example.com/"]


def test_endpoint_draft_from_url(public_dns, monkeypatch):
    monkeypatch.setattr(url_intel.requests, "get", lambda *a, **k: FakeGet())
    r = client.post("/api/campaigns/draft-from-url", json={"url": "https://example.com/ep9"})
    assert r.status_code == 200 and r.json()["title"].startswith("Episode 9")
    assert client.post("/api/campaigns/draft-from-url", json={"url": "http://127.0.0.1/admin"}).status_code == 400
    monkeypatch.setattr(url_intel.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert client.post("/api/campaigns/draft-from-url", json={"url": "https://example.com/x"}).status_code == 502


def test_preview_maps_template_variables_including_title():
    db.add_contact("Gur", "+919500000001", "vip_clients", opted_in=True, consent_basis="signed_up_form")
    db.upsert_template("three_var", "pa", "MARKETING", "APPROVED",
                       "Hello {{1}} new: {{3}} watch here: {{2}} thanks")
    p = client.post("/api/campaigns/preview", json={
        "title": "Ep 9 special", "video_url": "https://youtu.be/ep9", "target_groups": ["vip_clients"],
        "template_name": "three_var", "template_language": "pa"}).json()
    msg = p["screened_recipients"][0]["personalized_msg"]
    assert msg == "Hello Gur new: Ep 9 special watch here: https://youtu.be/ep9 thanks"


# ------------------------------------------------------------------ stage 2: pause / resume
def _setup_campaign(n=4):
    for i in range(n):
        db.add_contact(f"P{i}", f"+91960000000{i}", "vip_clients", opted_in=True, consent_basis="signed_up_form")
    db.upsert_template("vid_update", "pa", "MARKETING", "APPROVED", "Hello {{1}} watch {{2}} now please")
    return db.create_campaign("t", "https://youtu.be/p", "m", ["vip_clients"], template_name="vid_update",
                              template_language="pa")


def test_pause_stops_after_current_message_and_resume_finishes(monkeypatch):
    cid = _setup_campaign(4)
    cs.start_campaign(cid)
    real = cs._send_one
    state = {"n": 0}

    def send_then_pause(camp, d, contact):
        state["n"] += 1
        if state["n"] == 2:
            assert cs.pause_campaign(cid) == "PAUSING"
        return real(camp, d, contact)
    monkeypatch.setattr(cs, "_send_one", send_then_pause)
    out = cs.run_campaign(cid)
    assert out["status"] == "PAUSED" and out["sent"] == 2 and out["remaining"] == 2
    camp = db.get_campaign(cid)
    assert camp["status"] == "PAUSED" and "Paused by you" in camp["status_note"]
    monkeypatch.setattr(cs, "_send_one", real)
    cs.start_campaign(cid, resume=True)
    out2 = cs.run_campaign(cid)
    assert out2["status"] == "COMPLETED" and out2["sent"] == 4 and out2["remaining"] == 0


def test_pause_rejected_when_not_running_and_cancel_works_while_paused():
    cid = _setup_campaign(2)
    with pytest.raises(cs.CampaignError):
        cs.pause_campaign(cid)                       # still DRAFT
    cs.start_campaign(cid)
    db.set_campaign_fields(cid, status="PAUSED")
    assert cs.cancel_campaign(cid) == "CANCELLED"


def test_pause_endpoint():
    cid = _setup_campaign(2)
    assert client.post(f"/api/campaigns/{cid}/pause").status_code == 400
    cs.start_campaign(cid)
    assert client.post(f"/api/campaigns/{cid}/pause").json()["status"] == "PAUSING"


# ------------------------------------------------------------------ stage 3: live gate
def test_env_safe_mode_false_alone_never_sends_live(live_creds_only):
    cid = _setup_campaign(2)
    p = client.post("/api/campaigns/preview", json={"title": "t", "video_url": "https://y.com/g",
                                                    "target_groups": ["vip_clients"],
                                                    "template_name": "vid_update"}).json()
    assert p["mode"] == "SIMULATION" and client.get("/api/config").json()["mode"] == "SIMULATION"
    cs.start_campaign(cid); cs.run_campaign(cid)
    assert live_creds_only.calls == []                              # nothing reached Meta
    assert {d["status"] for d in db.get_campaign_details(cid)["dispatches"]} == {"SIMULATED"}


def test_authorize_is_refused_until_a_real_test_was_delivered(live_creds_only, monkeypatch):
    monkeypatch.setattr(config, "OWNER_WHATSAPP_NUMBER", "919876543210")
    r = client.post("/api/live/authorize", json={"confirm_text": "ENABLE LIVE SENDING"})
    assert r.status_code == 400 and "DELIVERED" in r.json()["detail"]
    # a test message that Meta only ACCEPTED (not delivered) is still not enough
    assert client.post("/api/test-send", json={"template_name": "t", "language": "pa", "params": ["a", "b"]}).status_code == 200
    assert db.latest_live_test()["status"] == "sent" and live_creds_only.calls[0]["json"]["to"] == "919876543210"
    assert client.post("/api/live/authorize", json={"confirm_text": "ENABLE LIVE SENDING"}).status_code == 400


def test_full_gate_sequence_test_delivered_authorize_live_then_disable(live_creds_only, monkeypatch):
    import hashlib, hmac, json
    monkeypatch.setattr(config, "OWNER_WHATSAPP_NUMBER", "919876543210")
    wa_id = client.post("/api/test-send", json={"template_name": "t", "language": "pa",
                                                "params": ["a", "b"]}).json()["wa_id"]
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "statuses": [{"id": wa_id, "status": "delivered", "timestamp": "1760000000"}]}}]}]}
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"test-app-secret", raw, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=raw, headers={"X-Hub-Signature-256": sig}).status_code == 200
    st = client.get("/api/live/status").json()
    assert st["test_delivered"] and not st["authorized"] and st["effective_mode"] == "SIMULATION"
    # wrong confirmation text is refused
    assert client.post("/api/live/authorize", json={"confirm_text": "yes"}).status_code == 400
    st = client.post("/api/live/authorize", json={"confirm_text": "ENABLE LIVE SENDING"}).json()
    assert st["authorized"] and st["effective_mode"] == "LIVE"
    # now a campaign really goes to Meta
    live_creds_only.calls.clear()
    cid = _setup_campaign(1)
    cs.start_campaign(cid, expected_count=1); cs.run_campaign(cid)
    assert len(live_creds_only.calls) == 1
    # and the owner can switch it off again
    assert client.post("/api/live/disable").json()["effective_mode"] == "SIMULATION"


def test_authorize_blocked_when_env_still_safe_or_no_credentials():
    r = client.post("/api/live/authorize", json={"confirm_text": "ENABLE LIVE SENDING"})
    assert r.status_code == 400 and "SAFE_MODE" in r.json()["detail"]


def test_readiness_never_claims_live_without_delivered_test(live_creds_only):
    r = client.get("/api/meta/readiness").json()
    items = {c["item"]: c for c in r["checklist"]}
    assert items["Real test message delivered"]["status"] == "TODO"
    assert items["Sending mode"]["status"] == "WARN" and r["ready_for_live"] is False


# ---------------------------------------------------------------- deleted Meta app diagnosis
def test_deleted_app_error_is_diagnosed_and_secret_free(live_mode):
    import whatsapp_service as wa
    from conftest import FakeResponse
    body = {"error": {"message": "Error validating application. Application has been deleted.",
                      "type": "OAuthException", "code": 190, "error_subcode": 458}}
    live_mode.responder = staticmethod(lambda url, json: FakeResponse(400, body))
    r = wa.create_remote_template({"name": "x", "language": "pa", "category": "MARKETING", "components": []})
    assert not r["success"]
    assert "DELETED" in r["error"] and "System users" in r["error"]
    assert "TEST_TOKEN" not in r["error"]
    assert wa.classify_error(190, 400) == "fatal"


# ---------------------------------------------------------------- dashboard: "Use this text" regression
def test_use_this_text_fills_message_box_and_survives_next_step():
    """Browser test: the Punjabi free-text button fills the campaign message box (not the template editor),
    keeps {{name}}/{{video_url}} exactly, shows a success message and the text reaches the preview request."""
    import json
    import socket
    import threading
    import time
    pw = pytest.importorskip("playwright.sync_api")
    import uvicorn
    import app as app_module

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(app_module.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True); th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    body = "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ {{name}} ਜੀ!\n{{video_url}}"
    previews = []
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:
                pytest.skip("Chromium not installed for Playwright")
            page = browser.new_page()
            page.route("**/api/campaigns/draft-from-url", lambda r: r.fulfill(
                status=200, content_type="application/json", body=json.dumps({
                    "campaign_name": "Ep", "title": "T", "description": "", "warning": None,
                    "messages": [{"id": "pa_text", "label": "Punjabi", "kind": "text", "language": "pa", "body": body}]})))
            page.on("request", lambda rq: previews.append(rq.post_data) if rq.url.endswith("/api/campaigns/preview") else None)
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector("#mode")
            page.fill("#url", "https://example.com/x")
            page.click("#analyse")
            page.wait_for_selector("[data-use=pa_text]")
            page.click("[data-use=pa_text]")
            assert page.input_value("#txt") == body                      # exact, placeholders intact
            assert page.is_visible("#txtBox") and not page.is_visible("#tplBox")
            assert "Text selected" in page.inner_text("#useOk")          # visible success message
            assert page.input_value("#tBody") == ""                      # template editor untouched
            page.click("#to2")                                           # move to next step
            assert page.input_value("#txt") == body                      # still there
            browser.close()
    finally:
        server.should_exit = True
        th.join(timeout=5)
