"""
Official Meta WhatsApp Cloud API client.

* send_template_message  - the only way to START a conversation (marketing / utility)
* send_whatsapp_message  - free-text, valid only inside the 24h customer-service window
* list_remote_templates / create_remote_template - manage templates from the dashboard
* get_phone_number_info / get_waba_subscriptions  - account readiness checks

Every function returns a plain dict: {"success": bool, ...}. Nothing here raises for API errors,
and no secret is ever written to a log or returned to the browser.
"""

import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

import config
from phone_utils import api_phone, normalize_phone

# ---------------------------------------------------------------- error classification
# Meta error code -> how the campaign engine must react.
#   fatal     : stop the whole campaign (credentials / account / template problem)
#   retry     : transient, retry with back-off
#   permanent : this recipient cannot be messaged; continue with the next one
FATAL_CODES = {
    "190": "Access token is invalid or expired - create a new System User token.",
    "10": "Permission denied - token lacks whatsapp_business_messaging permission.",
    "200": "Permission error for this WhatsApp Business Account.",
    "368": "Account temporarily blocked for policy violations.",
    "131031": "Business account is locked/restricted by Meta.",
    "131042": "Payment method problem - add/repair billing in WhatsApp Manager.",
    "131048": "Spam rate limit hit - Meta throttled this number. Pause and review content.",
    "133010": "Phone number is not registered with the Cloud API.",
    "132000": "Template parameter count does not match the approved template.",
    "132001": "Template does not exist in this language/name.",
    "132005": "Translated template text is too long.",
    "132007": "Template format character policy violated.",
    "132012": "Template parameter format mismatch.",
    "132015": "Template is paused due to quality.",
    "132016": "Template is disabled.",
}
RETRY_CODES = {"130429", "131000", "131016", "80007", "4", "2", "1", "131057"}
PERMANENT_CODES = {
    "131026": "Recipient is not a WhatsApp user or cannot receive this message.",
    "131030": "Recipient not in the allowed list (test number only sends to verified recipients).",
    "131047": "Outside the 24h window - a template message is required.",
    "131049": "Meta chose not to deliver this marketing message (per-user marketing limit).",
    "131056": "Too many messages to this same recipient in a short time.",
    "131021": "Recipient number equals the sender number.",
    "131051": "Unsupported message type.",
}


def classify_error(code: Any, http_status: int = 0) -> str:
    c = str(code or "")
    if c in FATAL_CODES:
        return "fatal"
    if c in RETRY_CODES or http_status in (429, 500, 502, 503, 504):
        return "retry"
    if c in PERMANENT_CODES:
        return "permanent"
    if http_status in (401, 403):
        return "fatal"
    return "permanent"


def diagnose_error(code: Any, subcode: Any, message: str) -> str:
    """Plain-language diagnosis for errors that need action in the Meta dashboards (never includes secrets)."""
    m = (message or "").lower()
    if "application has been deleted" in m or "error validating application" in m:
        return ("DIAGNOSIS: the access token in .env (WHATSAPP_TOKEN) belongs to a Meta Developer App that has been "
                "DELETED (or the app no longer exists). Nothing is wrong with the template. "
                "FIX in Meta: (1) developers.facebook.com/apps -> check the app list incl. 'Recently deleted' "
                "(apps can be restored within 30 days); or open an existing live app that has the WhatsApp product "
                "linked to your WhatsApp Business Account. (2) business.facebook.com -> Business settings -> Users -> "
                "System users -> your system user -> Generate token, choose THAT existing app, tick "
                "whatsapp_business_management + whatsapp_business_messaging. (3) Put the new token in .env "
                "(WHATSAPP_TOKEN), also META_APP_SECRET from that app, restart. Your WhatsApp account and number are untouched.")
    if str(code) == "190":
        return ("DIAGNOSIS: the token was rejected (expired, revoked, or its app/user was removed). "
                "Generate a new System User token in Meta Business settings and update WHATSAPP_TOKEN in .env.")
    return ""


def explain_error(code: Any, fallback: str = "") -> str:
    c = str(code or "")
    return FATAL_CODES.get(c) or PERMANENT_CODES.get(c) or fallback


# ---------------------------------------------------------------- helpers
def sanitize_phone_number(raw_phone: str) -> str:
    return re.sub(r"[^\d]", "", raw_phone or "")


def generate_click_to_chat_link(phone: str, message: str) -> str:
    """Official wa.me link - only a convenience for manual testing, not used for bulk sending."""
    return f"https://wa.me/{sanitize_phone_number(phone)}?text={urllib.parse.quote(message)}"


def _base() -> str:
    return f"https://graph.facebook.com/{config.META_GRAPH_VERSION}"


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}", "Content-Type": "application/json"}


def clean_template_param(value: str, limit: int = 1000) -> str:
    """Meta rejects newlines, tabs and 4+ consecutive spaces inside template parameters."""
    v = re.sub(r"[\r\n\t]+", " ", value or "")
    v = re.sub(r" {2,}", " ", v).strip()
    return v[:limit] or "-"


def _parse_response(resp: requests.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.ok and data.get("messages"):
        return {"success": True, "wa_id": data["messages"][0].get("id", ""), "mode": "OFFICIAL_CLOUD_API"}
    err = data.get("error", {}) if isinstance(data, dict) else {}
    code = err.get("code")
    sub = (err.get("error_data") or {}).get("details") or ""
    message = err.get("message") or resp.text[:200]
    diag = diagnose_error(code, err.get("error_subcode"), message)
    return {
        "success": False, "wa_id": None, "mode": "OFFICIAL_CLOUD_API",
        "error_code": str(code or resp.status_code),
        "error": (f"Meta API error {code or resp.status_code}: {diag or explain_error(code, message)} {sub}").strip()
                 + (f" (Meta said: {message})" if diag else ""),
        "action": classify_error(code, resp.status_code),
    }


def _post_with_retry(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    last: Dict[str, Any] = {}
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            resp = requests.post(url, headers=_headers(), json=payload, timeout=20)
            last = _parse_response(resp)
        except requests.exceptions.RequestException as e:
            last = {"success": False, "wa_id": None, "mode": "OFFICIAL_CLOUD_API", "error_code": "network",
                    "error": f"Network error reaching Meta: {type(e).__name__}", "action": "retry"}
        if last["success"] or last.get("action") != "retry" or attempt == config.MAX_RETRIES:
            return last
        time.sleep(min(2 ** attempt, 20))
    return last


def _simulated(phone: str, kind: str, preview: str) -> Dict[str, Any]:
    return {"success": True, "simulated": True, "mode": "SAFE_MODE",
            "wa_id": f"SIM_MSG_{int(time.time() * 1000)}_{sanitize_phone_number(phone)[-4:]}",
            "notes": f"Safe mode: nothing was sent to Meta ({kind}).", "preview": preview}


# ---------------------------------------------------------------- sending
def send_template_message(phone: str, template_name: str, language: str, body_params: List[str],
                          button_url_param: Optional[str] = None) -> Dict[str, Any]:
    to = api_phone(phone)
    if len(to) < 10:
        return {"success": False, "error": f"Invalid phone number: '{phone}'", "error_code": "invalid_phone",
                "action": "permanent", "mode": "validation_error", "wa_id": None}
    params = [clean_template_param(p) for p in body_params]
    if not config.is_cloud_api_configured():
        return _simulated(phone, "template", f"{template_name}[{language}] {params}")

    components: List[Dict[str, Any]] = []
    if params:
        components.append({"type": "body", "parameters": [{"type": "text", "text": p} for p in params]})
    if button_url_param:
        components.append({"type": "button", "sub_type": "url", "index": "0",
                           "parameters": [{"type": "text", "text": clean_template_param(button_url_param, 200)}]})
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "template",
               "template": {"name": template_name, "language": {"code": language}, "components": components}}
    return _post_with_retry(f"{_base()}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages", payload)


def send_whatsapp_message(phone: str, message: str, preview_url: bool = True) -> Dict[str, Any]:
    """Free-text message. Meta only delivers it if the recipient wrote to you in the last 24 hours."""
    to = api_phone(phone)
    if len(to) < 10:
        return {"success": False, "error": f"Invalid phone number format: '{phone}'",
                "error_code": "invalid_phone", "action": "permanent", "wa_id": None, "mode": "validation_error"}
    if not config.is_cloud_api_configured():
        r = _simulated(phone, "text", message[:80])
        r["click_to_chat_url"] = generate_click_to_chat_link(phone, message)
        return r
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
               "text": {"preview_url": preview_url, "body": message}}
    return _post_with_retry(f"{_base()}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages", payload)


# ---------------------------------------------------------------- templates
def _get(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        resp = requests.get(url, headers=_headers(), params=params or {}, timeout=20)
        data = resp.json() if resp.content else {}
    except (requests.exceptions.RequestException, ValueError) as e:
        return {"success": False, "error": f"Network/parse error: {type(e).__name__}"}
    if resp.ok:
        return {"success": True, "data": data}
    err = data.get("error", {}) if isinstance(data, dict) else {}
    diag = diagnose_error(err.get("code"), err.get("error_subcode"), err.get("message", ""))
    return {"success": False, "error_code": str(err.get("code", resp.status_code)),
            "error": diag or explain_error(err.get("code"), err.get("message", resp.text[:200]))}


def list_remote_templates() -> Dict[str, Any]:
    if not (config.WHATSAPP_TOKEN and config.WHATSAPP_BUSINESS_ACCOUNT_ID):
        return {"success": False, "error": "WHATSAPP_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID are required."}
    out: List[Dict[str, Any]] = []
    url = f"{_base()}/{config.WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates"
    params: Dict[str, Any] = {"limit": 100,
                              "fields": "id,name,language,status,category,components,rejected_reason"}
    for _ in range(10):  # follow pagination, bounded
        r = _get(url, params)
        if not r["success"]:
            return r
        out.extend(r["data"].get("data", []))
        nxt = (r["data"].get("paging") or {}).get("next")
        if not nxt:
            break
        url, params = nxt, {}
    return {"success": True, "templates": out}


def build_template_payload(name: str, language: str, category: str, body: str, footer: str = "",
                           example_values: Optional[List[str]] = None,
                           quick_reply_stop: bool = True) -> Dict[str, Any]:
    n_vars = len(set(re.findall(r"\{\{(\d+)\}\}", body)))
    body_comp: Dict[str, Any] = {"type": "BODY", "text": body}
    if n_vars:
        ex = (example_values or ["Manjot", "https://example.com"])[:n_vars]
        while len(ex) < n_vars:
            ex.append("example")
        body_comp["example"] = {"body_text": [ex]}
    comps: List[Dict[str, Any]] = [body_comp]
    if footer:
        comps.append({"type": "FOOTER", "text": footer})
    if quick_reply_stop:
        comps.append({"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "STOP"}]})
    return {"name": name, "language": language, "category": category.upper(), "components": comps}


def validate_template_input(name: str, body: str) -> List[str]:
    problems = []
    if not re.fullmatch(r"[a-z0-9_]{1,512}", name or ""):
        problems.append("Template name: only lowercase letters, numbers and underscores.")
    if len(body or "") > 1024:
        problems.append("Body is longer than 1024 characters.")
    nums = sorted(int(x) for x in re.findall(r"\{\{(\d+)\}\}", body or ""))
    if nums and nums != list(range(1, max(nums) + 1)):
        problems.append("Variables must be numbered {{1}}, {{2}}, ... without gaps.")
    if re.match(r"\s*\{\{", body or "") or re.search(r"\}\}\s*$", body or ""):
        problems.append("Meta does not allow a variable at the very start or very end of the body.")
    if len(body or "") < 10:
        problems.append("Body is too short.")
    return problems


def create_remote_template(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not (config.WHATSAPP_TOKEN and config.WHATSAPP_BUSINESS_ACCOUNT_ID):
        return {"success": False, "error": "WHATSAPP_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID are required."}
    try:
        resp = requests.post(f"{_base()}/{config.WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates",
                             headers=_headers(), json=payload, timeout=30)
        data = resp.json() if resp.content else {}
    except (requests.exceptions.RequestException, ValueError) as e:
        return {"success": False, "error": f"Network/parse error: {type(e).__name__}"}
    if resp.ok:
        return {"success": True, "meta_id": data.get("id", ""), "status": data.get("status", "PENDING"),
                "category": data.get("category", payload.get("category"))}
    err = data.get("error", {})
    msg = err.get("error_user_msg") or err.get("message") or resp.text[:300]
    diag = diagnose_error(err.get("code"), err.get("error_subcode"), err.get("message") or msg)
    return {"success": False, "error_code": str(err.get("code", resp.status_code)),
            "error": f"{diag} (Meta said: {msg})" if diag else msg}


def flatten_template(t: Dict[str, Any]) -> Dict[str, str]:
    body = header = footer = buttons = ""
    for c in t.get("components", []):
        kind = c.get("type")
        if kind == "BODY":
            body = c.get("text", "")
        elif kind == "HEADER":
            header = c.get("text", "") or c.get("format", "")
        elif kind == "FOOTER":
            footer = c.get("text", "")
        elif kind == "BUTTONS":
            buttons = ", ".join(b.get("text", "") for b in c.get("buttons", []))
    return {"body": body, "header": header, "footer": footer, "buttons": buttons}


# ---------------------------------------------------------------- account readiness
def get_phone_number_info() -> Dict[str, Any]:
    if not config.has_credentials():
        return {"success": False, "error": "Token / phone number id not configured."}
    return _get(f"{_base()}/{config.WHATSAPP_PHONE_NUMBER_ID}", {
        "fields": "display_phone_number,verified_name,quality_rating,code_verification_status,"
                  "name_status,platform_type,throughput,messaging_limit_tier,status"})


def get_waba_subscriptions() -> Dict[str, Any]:
    if not (config.WHATSAPP_TOKEN and config.WHATSAPP_BUSINESS_ACCOUNT_ID):
        return {"success": False, "error": "WABA id / token not configured."}
    return _get(f"{_base()}/{config.WHATSAPP_BUSINESS_ACCOUNT_ID}/subscribed_apps")


def subscribe_app_to_waba() -> Dict[str, Any]:
    """Tell Meta to deliver this WABA's webhooks to the app. Needed once."""
    if not (config.WHATSAPP_TOKEN and config.WHATSAPP_BUSINESS_ACCOUNT_ID):
        return {"success": False, "error": "WABA id / token not configured."}
    try:
        resp = requests.post(f"{_base()}/{config.WHATSAPP_BUSINESS_ACCOUNT_ID}/subscribed_apps",
                             headers=_headers(), timeout=20)
        data = resp.json() if resp.content else {}
    except (requests.exceptions.RequestException, ValueError) as e:
        return {"success": False, "error": f"Network/parse error: {type(e).__name__}"}
    return {"success": bool(data.get("success")), "raw": data if not resp.ok else None}


def check_number_format(raw: str) -> Optional[str]:
    return normalize_phone(raw)
