"""
FastAPI backend + dashboard for the WhatsApp Cloud API automation.

Run:  uvicorn app:app --port 8000
Public routes: /webhook (Meta) and /health. Everything else is protected (see _guard).
"""

import base64
import hmac
import json
import os
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import campaign_service as cs
import config
import contact_import
import database as db
import url_intel
import whatsapp_service as wa
from meta_compliance import OFFICIAL_VIDEO_TEMPLATE_SPEC, check_meta_readiness
from webhook import router as webhook_router

app = FastAPI(title="WhatsApp Cloud API Automation")
app.include_router(webhook_router)

db.init_db()
db.seed_isolated_dummy_contacts()
db.recover_interrupted_campaigns()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

PUBLIC_PATHS = ("/webhook", "/health")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "testserver", "testclient")

CONSENT_BASES = {
    "asked_to_receive": "They asked me to send them my updates / videos",
    "signed_up_form": "They signed up through my form, website or channel",
    "existing_customer_optin": "Existing customer who agreed to receive WhatsApp updates",
    "inbound_message": "They messaged me first asking for this",
}


# ---------------------------------------------------------------- protection
@app.middleware("http")
async def _guard(request: Request, call_next):
    path = request.url.path
    if path.startswith(PUBLIC_PATHS):
        return await call_next(request)
    if config.DASHBOARD_PASSWORD:
        header = request.headers.get("authorization", "")
        ok = False
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = (hmac.compare_digest(user, config.DASHBOARD_USER)
                      and hmac.compare_digest(pw, config.DASHBOARD_PASSWORD))
            except Exception:
                ok = False
        if not ok:
            return JSONResponse({"detail": "Authentication required"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="WhatsApp dashboard"'})
    else:
        raw_host = request.headers.get("host") or ""
        host = (raw_host.split("]")[0] + "]") if raw_host.startswith("[") else raw_host.split(":")[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Set DASHBOARD_PASSWORD to open the dashboard from another address."},
                                status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------- models
class CampaignPreviewRequest(BaseModel):
    title: str
    video_url: str
    message_template: Optional[str] = None
    target_groups: List[str] = []
    is_test_only: Optional[bool] = False
    template_name: Optional[str] = None
    template_language: Optional[str] = None
    template_params: Optional[List[str]] = None
    contact_ids: Optional[List[int]] = None


class CampaignApprovalRequest(BaseModel):
    confirmed: bool
    expected_count: Optional[int] = None


class ContactCreateRequest(BaseModel):
    name: str
    phone: str
    email: Optional[str] = ""
    group_id: str
    opted_in: bool = False
    opt_in_channel: Optional[str] = "web_portal"
    consent_basis: Optional[str] = None
    tags: Optional[str] = ""
    is_test: Optional[bool] = False


class ConsentRequest(BaseModel):
    contact_ids: List[int]
    basis: str
    attest: bool = False
    note: Optional[str] = ""


class RevokeRequest(BaseModel):
    contact_ids: List[int]


class ImportRequest(BaseModel):
    csv_text: str
    group_id: str = "content_subscribers"


class GroupUpdateRequest(BaseModel):
    invite_link: str
    name: Optional[str] = None
    description: Optional[str] = None


class CommunityUpdateRequest(BaseModel):
    community_name: str
    announcement_invite_link: str
    description: str
    guidelines: str


class TemplateCreateRequest(BaseModel):
    name: str
    language: str = config.DEFAULT_TEMPLATE_LANGUAGE
    category: str = "MARKETING"
    body: str
    footer: Optional[str] = ""
    example_values: Optional[List[str]] = None
    quick_reply_stop: bool = True


class UrlRequest(BaseModel):
    url: str


class LiveAuthRequest(BaseModel):
    confirm_text: str


class TestSendRequest(BaseModel):
    template_name: str
    language: Optional[str] = None
    params: List[str] = []


# ---------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index_page():
    for name in ("dashboard.html",):
        p = os.path.join(STATIC_DIR, name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})
    return "<h1>Backend running. static/dashboard.html not found.</h1>"


# ---------------------------------------------------------------- config / readiness
@app.get("/api/config")
def get_system_config():
    return {
        "is_cloud_api_configured": config.is_cloud_api_configured(),
        "live_authorized": bool(db.get_state("live_authorized_at")),
        "mode": "LIVE" if cs.live_enabled() else "SIMULATION",
        "graph_version": config.META_GRAPH_VERSION,
        "phone_number_id": config.mask(config.WHATSAPP_PHONE_NUMBER_ID) or "Not configured",
        "safe_mode": config.FORCE_SAFE_MODE,
        "daily_recipient_limit": config.DAILY_RECIPIENT_LIMIT,
        "default_template": config.DEFAULT_TEMPLATE,
        "default_language": config.DEFAULT_TEMPLATE_LANGUAGE,
        "owner_test_number_set": bool(config.OWNER_WHATSAPP_NUMBER),
    }


@app.get("/api/meta/readiness")
def get_meta_readiness(live: bool = False):
    return check_meta_readiness(live=live)


@app.post("/api/webhook/subscribe")
def subscribe_webhook():
    r = wa.subscribe_app_to_waba()
    if not r["success"]:
        raise HTTPException(502, r.get("error", "Meta did not accept the subscription."))
    return r


# ---------------------------------------------------------------- groups / community
@app.get("/api/groups")
def list_groups():
    return db.get_all_groups()


@app.put("/api/groups/{group_id}")
def update_group_link(group_id: str, req: GroupUpdateRequest):
    if req.name and req.description:
        db.update_group_details(group_id, req.name, req.description, req.invite_link)
    else:
        db.update_group_invite_link(group_id, req.invite_link)
    return {"success": True, "group_id": group_id, "invite_link": req.invite_link}


@app.get("/api/community")
def get_community():
    return db.get_community_info()


@app.put("/api/community")
def save_community(req: CommunityUpdateRequest):
    db.update_community_info(req.community_name, req.announcement_invite_link, req.description, req.guidelines)
    return {"success": True, "community": db.get_community_info()}


# ---------------------------------------------------------------- contacts & consent
@app.get("/api/contacts")
def list_contacts(group_id: Optional[str] = None, only_opted_in: bool = False, is_test: Optional[bool] = None):
    return db.get_contacts(group_id=group_id, only_opted_in=only_opted_in, is_test=is_test)


@app.get("/api/contacts/search")
def search_contacts(q: str = "", status: str = "all", group_id: Optional[str] = None,
                    limit: int = 100, offset: int = 0):
    return db.search_contacts(q=q, status=status, group_id=group_id, limit=min(limit, 500), offset=offset)


@app.post("/api/contacts")
def create_new_contact(req: ContactCreateRequest):
    from phone_utils import normalize_phone
    phone = normalize_phone(req.phone)
    if not phone and not req.is_test:
        raise HTTPException(400, "Phone number is not valid.")
    if req.opted_in and not req.consent_basis:
        raise HTTPException(400, "To mark someone as opted in you must say how they agreed (consent_basis).")
    if req.consent_basis and req.consent_basis not in CONSENT_BASES:
        raise HTTPException(400, "Unknown consent_basis.")
    cid = db.add_contact(name=req.name, phone=phone or req.phone, group_id=req.group_id, opted_in=req.opted_in,
                         email=req.email or "", channel=req.opt_in_channel or "web_portal", tags=req.tags or "",
                         is_test=req.is_test or False, consent_basis=req.consent_basis)
    return {"success": True, "contact_id": cid}


@app.get("/api/consent/bases")
def consent_bases():
    return CONSENT_BASES


@app.get("/api/consent/summary")
def consent_summary():
    return db.consent_summary()


@app.post("/api/contacts/import")
def import_contacts_endpoint(req: ImportRequest):
    if req.group_id not in [g["id"] for g in db.get_all_groups()]:
        raise HTTPException(400, "Unknown group")
    if len(req.csv_text) > 5_000_000:
        raise HTTPException(413, "CSV too large")
    result = contact_import.import_csv_text(req.csv_text, req.group_id, source="dashboard_import")
    return {"success": True, **result,
            "note": "Imported contacts are NOT opted in. Record their consent before messaging them."}


@app.post("/api/contacts/consent")
def record_consent(req: ConsentRequest):
    if not req.attest:
        raise HTTPException(400, "You must confirm that these people agreed to receive WhatsApp messages from you.")
    if req.basis not in CONSENT_BASES:
        raise HTTPException(400, "Choose how these people agreed (basis).")
    if not req.contact_ids:
        raise HTTPException(400, "No contacts selected.")
    n = db.set_consent(req.contact_ids, req.basis, req.note or "")
    return {"success": True, "updated": n, "skipped_opted_out": len(req.contact_ids) - n}


@app.post("/api/contacts/revoke")
def revoke(req: RevokeRequest):
    return {"success": True, "updated": db.revoke_consent(req.contact_ids, "revoked_by_owner")}


# ---------------------------------------------------------------- templates
@app.get("/api/templates")
def get_templates():
    return {"templates": db.list_templates(), "suggested": OFFICIAL_VIDEO_TEMPLATE_SPEC}


@app.post("/api/templates/sync")
def sync_templates():
    r = wa.list_remote_templates()
    if not r["success"]:
        raise HTTPException(502, r.get("error", "Could not read templates from Meta."))
    for t in r["templates"]:
        flat = wa.flatten_template(t)
        db.upsert_template(t.get("name", ""), t.get("language", ""), t.get("category", ""), t.get("status", ""),
                           flat["body"], flat["header"], flat["footer"], flat["buttons"], t.get("id", ""),
                           t.get("rejected_reason") or "")
    return {"success": True, "count": len(r["templates"])}


@app.post("/api/templates")
def create_template(req: TemplateCreateRequest):
    problems = wa.validate_template_input(req.name, req.body)
    if problems:
        raise HTTPException(400, " ".join(problems))
    payload = wa.build_template_payload(req.name, req.language, req.category, req.body, req.footer or "",
                                        req.example_values, req.quick_reply_stop)
    r = wa.create_remote_template(payload)
    if not r["success"]:
        raise HTTPException(502, r.get("error", "Meta rejected the template."))
    db.upsert_template(req.name, req.language, r.get("category") or req.category, r.get("status", "PENDING"),
                       req.body, "", req.footer or "", "STOP" if req.quick_reply_stop else "", r.get("meta_id", ""))
    return {"success": True, "status": r.get("status", "PENDING"), "meta_id": r.get("meta_id", "")}


@app.post("/api/test-send")
def test_send(req: TestSendRequest):
    """Send ONE approved template to the owner's own number, to verify the setup end to end."""
    if not config.OWNER_WHATSAPP_NUMBER:
        raise HTTPException(400, "Set OWNER_WHATSAPP_NUMBER in .env (your own number) to use this button.")
    r = wa.send_template_message(config.OWNER_WHATSAPP_NUMBER, req.template_name,
                                 req.language or config.DEFAULT_TEMPLATE_LANGUAGE, req.params)
    if not r["success"]:
        raise HTTPException(502, r.get("error", "Send failed"))
    if not r.get("simulated"):
        db.record_live_test(r["wa_id"], config.OWNER_WHATSAPP_NUMBER, req.template_name)
    return {"success": True, "simulated": bool(r.get("simulated")), "wa_id": r.get("wa_id"),
            "next": None if r.get("simulated") else "Check your WhatsApp. When Meta reports 'delivered' "
                    "(webhook), the Setup tab will let you authorise live sending."}


# ---------------------------------------------------------------- live-sending gate
def _live_status() -> dict:
    test = db.latest_live_test()
    return {
        "credentials": config.has_credentials(),
        "safe_mode_env": config.FORCE_SAFE_MODE,
        "can_send_test": config.is_cloud_api_configured() and bool(config.OWNER_WHATSAPP_NUMBER),
        "last_test": ({"status": test["status"], "template": test["template_name"], "to_tail": test["to_tail"],
                       "created_at": test["created_at"], "error": test["error_details"]} if test else None),
        "test_delivered": db.has_delivered_live_test(),
        "authorized": bool(db.get_state("live_authorized_at")),
        "authorized_at": db.get_state("live_authorized_at"),
        "effective_mode": "LIVE" if cs.live_enabled() else "SIMULATION",
    }


@app.get("/api/live/status")
def live_status():
    return _live_status()


@app.post("/api/live/authorize")
def live_authorize(req: LiveAuthRequest):
    st = _live_status()
    if req.confirm_text.strip() != "ENABLE LIVE SENDING":
        raise HTTPException(400, 'Type exactly: ENABLE LIVE SENDING')
    if not st["credentials"] or st["safe_mode_env"]:
        raise HTTPException(400, "Set your Meta credentials and SAFE_MODE=false in .env first, then restart.")
    if not st["test_delivered"]:
        raise HTTPException(400, "Live sending stays locked until a real test message to your own number has been "
                                 "DELIVERED (confirmed by Meta's webhook). Use 'Send test to yourself' first.")
    db.set_state("live_authorized_at", db.now_iso())
    return _live_status()


@app.post("/api/live/disable")
def live_disable():
    db.set_state("live_authorized_at", None)
    return _live_status()


# ---------------------------------------------------------------- link -> campaign draft
@app.post("/api/campaigns/draft-from-url")
def draft_from_url(req: UrlRequest):
    try:
        return url_intel.analyse(req.url.strip())
    except url_intel.UnsafeURL as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not read that link ({type(e).__name__}). You can still type the name "
                                 "and message yourself.")


# ---------------------------------------------------------------- campaigns
def _group_screen(screen: dict) -> dict:
    def pack(key):
        return [{"contact": i["contact"], "reason": i.get("reason", "")} for i in screen[key]]
    return {
        "screened_recipients": [
            {"contact": i["contact"], "personalized_msg": i["personalized_msg"], "mode": i["mode"],
             "click_to_chat_url": i.get("click_to_chat_url", "")} for i in screen["ready"]],
        "skipped_no_consent": pack("skipped_no_consent") + pack("skipped_opted_out"),
        "skipped_unverified": pack("skipped_unverified"),
        "skipped_duplicate": pack("skipped_duplicate"),
        "skipped_invalid": pack("skipped_invalid"),
        "needs_template": pack("needs_template"),
        "deferred": pack("deferred"),
    }


@app.post("/api/campaigns/preview")
def preview_campaign(req: CampaignPreviewRequest):
    if not req.video_url or not req.video_url.startswith(("http://", "https://")):
        raise HTTPException(400, "A valid URL starting with http:// or https:// is required.")
    if not req.target_groups and not req.contact_ids:
        raise HTTPException(400, "Choose at least one group or select contacts.")
    if req.template_name and not req.template_language:
        tpl = db.get_template(req.template_name)
        req.template_language = (tpl or {}).get("language") or config.DEFAULT_TEMPLATE_LANGUAGE

    if req.template_name and not req.template_params:
        tpl = db.get_template(req.template_name, req.template_language)
        try:
            req.template_params = url_intel.variables_for_template_body((tpl or {}).get("body", "")) or None
        except ValueError as e:
            raise HTTPException(400, str(e))

    cid = db.create_campaign(
        title=req.title, video_url=req.video_url, message_template=req.message_template or config.DEFAULT_TEMPLATE,
        target_groups=req.target_groups, is_test_run=bool(req.is_test_only), template_name=req.template_name,
        template_language=req.template_language, template_params=req.template_params, contact_ids=req.contact_ids)
    camp = db.get_campaign(cid)
    screen = cs.screen_campaign(camp)
    ready = len(screen["ready"])
    db.set_campaign_fields(cid, expected_count=ready)
    live = cs.live_enabled() and not req.is_test_only
    problem = cs.template_problem(camp) if live else None
    packed = _group_screen(screen)
    return {
        "campaign_id": cid, "title": req.title, "video_url": req.video_url,
        "template": camp["message_template"], "template_name": req.template_name,
        "is_test_run": bool(req.is_test_only),
        "mode": "LIVE" if live else "SIMULATION",
        **packed,
        "total_screened": screen["total_candidates"], "ready_count": ready,
        "no_consent_count": len(packed["skipped_no_consent"]) + len(packed["skipped_unverified"]),
        "duplicate_count": len(packed["skipped_duplicate"]),
        "needs_template_count": len(packed["needs_template"]), "deferred_count": len(packed["deferred"]),
        "capacity_remaining": screen["capacity_remaining"], "blocking_problem": problem,
    }


def _http(e: cs.CampaignError):
    return HTTPException(e.status_code, e.message)


@app.post("/api/campaigns/{campaign_id}/approve")
def approve_and_send_campaign(campaign_id: int, req: CampaignApprovalRequest, background: BackgroundTasks):
    camp = db.get_campaign(campaign_id)
    if not camp:
        raise HTTPException(404, "Campaign not found")
    if not req.confirmed:
        db.update_campaign_status(campaign_id, "CANCELLED")
        return {"success": False, "status": "CANCELLED", "message": "Campaign approval rejected by user."}
    live = cs.live_enabled() and not camp["is_test_run"]
    expected = req.expected_count if req.expected_count is not None else camp.get("expected_count")
    if live and req.expected_count is None:
        raise HTTPException(400, "expected_count is required for live sends (the number shown in the preview).")
    try:
        summary = cs.start_campaign(campaign_id, expected_count=expected)
    except cs.CampaignError as e:
        raise _http(e)
    background.add_task(cs.run_campaign, campaign_id)
    return {"success": True, "campaign_id": campaign_id, "status": "IN_PROGRESS",
            "mode": "LIVE" if live else "SIMULATION", **summary}


@app.post("/api/campaigns/{campaign_id}/resume")
def resume_campaign(campaign_id: int, req: CampaignApprovalRequest, background: BackgroundTasks):
    if not req.confirmed:
        raise HTTPException(400, "Confirmation required.")
    try:
        summary = cs.start_campaign(campaign_id, resume=True)
    except cs.CampaignError as e:
        raise _http(e)
    background.add_task(cs.run_campaign, campaign_id)
    return {"success": True, "campaign_id": campaign_id, "status": "IN_PROGRESS", **summary}


@app.post("/api/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: int):
    try:
        return {"success": True, "status": cs.pause_campaign(campaign_id)}
    except cs.CampaignError as e:
        raise _http(e)


@app.post("/api/campaigns/{campaign_id}/cancel")
def cancel_campaign(campaign_id: int):
    try:
        return {"success": True, "status": cs.cancel_campaign(campaign_id)}
    except cs.CampaignError as e:
        raise _http(e)


@app.get("/api/campaigns")
def list_all_campaigns():
    return db.get_campaigns()


@app.get("/api/campaigns/{campaign_id}")
def view_campaign(campaign_id: int):
    details = db.get_campaign_details(campaign_id)
    if not details:
        raise HTTPException(404, "Campaign not found")
    return details


# ---------------------------------------------------------------- analytics
@app.get("/api/analytics")
def get_analytics():
    return db.analytics()
