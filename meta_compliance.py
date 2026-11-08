"""
Meta WhatsApp Business readiness report + recommended template.
`check_meta_readiness(live=True)` also asks Meta (read-only GET requests) about the number and webhook.
"""

from typing import Any, Dict, List

import config
import database as db
import whatsapp_service as wa

# Punjabi marketing template matching the Cyber Singh message. {{1}} = name, {{2}} = link.
OFFICIAL_VIDEO_TEMPLATE_SPEC = {
    "name": "cyber_singh_video_update",
    "language": "pa",
    "category": "MARKETING",
    "body": ("ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ {{1}} ਜੀ! 🙏\n\n"
             "ਸਾਡੇ ਚੈਨਲ Cyber Singh AI ਦੀ ਨਵੀਂ ਵੀਡੀਓ ਤੁਹਾਡੇ ਲਈ ਤਿਆਰ ਹੈ। ਇੱਥੇ ਦੇਖੋ: {{2}}\n\n"
             "ਤੁਹਾਡੇ ਪਿਆਰ ਅਤੇ ਸਾਥ ਲਈ ਬਹੁਤ-ਬਹੁਤ ਧੰਨਵਾਦ! — Manjot Singh"),
    "footer": "Reply STOP to unsubscribe",
    "example_values": ["Manjot", "https://www.youtube.com/@CYBERSINGH_Ai"],
    "variables": ["name", "video_url"],
}


def _item(item: str, status: str, detail: str, action: str = "") -> Dict[str, str]:
    return {"item": item, "status": status, "detail": detail, "action": action}


def check_meta_readiness(live: bool = False) -> Dict[str, Any]:
    checks: List[Dict[str, str]] = []
    has_token = bool(config.WHATSAPP_TOKEN)
    has_phone = bool(config.WHATSAPP_PHONE_NUMBER_ID)
    has_waba = bool(config.WHATSAPP_BUSINESS_ACCOUNT_ID)

    checks.append(_item("Access token", "OK" if has_token else "TODO",
        "Configured." if has_token else "Missing WHATSAPP_TOKEN in .env.",
        "" if has_token else "Meta Business Settings -> System users -> Generate token "
                             "(whatsapp_business_messaging + whatsapp_business_management)."))
    checks.append(_item("Phone number ID", "OK" if has_phone else "TODO",
        config.mask(config.WHATSAPP_PHONE_NUMBER_ID) if has_phone else "Missing WHATSAPP_PHONE_NUMBER_ID.",
        "" if has_phone else "developers.facebook.com -> your app -> WhatsApp -> API Setup."))
    checks.append(_item("WhatsApp Business Account ID", "OK" if has_waba else "TODO",
        config.mask(config.WHATSAPP_BUSINESS_ACCOUNT_ID) if has_waba else "Missing WHATSAPP_BUSINESS_ACCOUNT_ID.",
        "" if has_waba else "Same API Setup page (needed for templates)."))
    authorized = bool(db.get_state("live_authorized_at"))
    delivered = db.has_delivered_live_test()
    checks.append(_item("Sending mode", "OK" if (authorized and not config.FORCE_SAFE_MODE) else "WARN",
        "LIVE: messages are really sent (authorised by you)." if (authorized and not config.FORCE_SAFE_MODE)
        else "SIMULATION: nothing reaches Meta until live sending is authorised.",
        "" if (authorized and not config.FORCE_SAFE_MODE) else
        "Steps: SAFE_MODE=false in .env -> send test to yourself -> wait for 'delivered' -> authorise."))
    checks.append(_item("Real test message delivered", "OK" if delivered else "TODO",
        "Meta confirmed delivery of a real test message." if delivered else
        "No real message has been confirmed as delivered yet. Live sending is claimed only after this is green.",
        "" if delivered else "Templates tab -> Send test to yourself (needs an approved template + webhook)."))

    phone_info: Dict[str, Any] = {}
    if live and has_token and has_phone:
        r = wa.get_phone_number_info()
        if r["success"]:
            phone_info = r["data"]
            nm = phone_info.get("name_status", "?")
            checks.append(_item("Sender number", "OK" if nm in ("APPROVED", "AVAILABLE_WITHOUT_REVIEW") else "WARN",
                f"{phone_info.get('display_phone_number','?')} - '{phone_info.get('verified_name','?')}' - "
                f"display name: {nm}, quality: {phone_info.get('quality_rating','?')}, "
                f"tier: {phone_info.get('messaging_limit_tier','?')}",
                "" if nm in ("APPROVED", "AVAILABLE_WITHOUT_REVIEW") else
                "Display name not approved yet: WhatsApp Manager -> Phone numbers."))
        else:
            checks.append(_item("Sender number", "ERROR", r.get("error", "Meta rejected the request."),
                                "Check token validity and that it can access this phone number id."))

    secret_ok = bool(config.META_APP_SECRET)
    token_ok = bool(config.WEBHOOK_VERIFY_TOKEN)
    checks.append(_item("Webhook verify token", "OK" if token_ok else "TODO",
        "Configured." if token_ok else "Set WEBHOOK_VERIFY_TOKEN (any random string you choose).",
        "" if token_ok else "Use the same value in Meta -> WhatsApp -> Configuration -> Webhook."))
    checks.append(_item("Webhook signature (App Secret)", "OK" if secret_ok else "TODO",
        "Incoming webhooks are signature-checked." if secret_ok
        else "META_APP_SECRET missing: webhooks will be REJECTED (unsigned webhooks are not trusted).",
        "" if secret_ok else "Meta app -> App settings -> Basic -> App secret -> put in .env."))
    if live and has_token and has_waba:
        subs = wa.get_waba_subscriptions()
        if subs["success"]:
            n = len(subs["data"].get("data", []))
            checks.append(_item("App subscribed to WABA webhooks", "OK" if n else "TODO",
                f"{n} app(s) subscribed." if n else "No app subscribed: delivery receipts will not arrive.",
                "" if n else "Press 'Subscribe webhook' on the Setup tab."))

    tpls = db.list_templates()
    approved = [t for t in tpls if (t.get("status") or "").upper() == "APPROVED"]
    checks.append(_item("Approved message template", "OK" if approved else "TODO",
        f"{len(approved)} approved of {len(tpls)} known." if tpls else "No templates synced yet.",
        "" if approved else "Templates tab -> create the suggested template, wait for APPROVED, then Sync."))

    checks.append(_item("Dashboard protection", "OK" if config.DASHBOARD_PASSWORD else "WARN",
        "Password required." if config.DASHBOARD_PASSWORD else "No password: only this computer can open the dashboard.",
        "" if config.DASHBOARD_PASSWORD else "Set DASHBOARD_PASSWORD before hosting it online."))
    checks.append(_item("Daily recipient limit", "INFO",
        f"{config.DAILY_RECIPIENT_LIMIT} unique people per rolling 24h (unverified businesses are limited to 250 by Meta).",
        "Complete Meta Business Verification to unlock higher tiers."))

    blocking = [c for c in checks if c["status"] in ("TODO", "ERROR")]
    return {"safe_mode_active": config.FORCE_SAFE_MODE, "credentials_configured": has_token and has_phone,
            "ready_for_live": not blocking and not config.FORCE_SAFE_MODE, "checklist": checks,
            "blocking": [c["item"] for c in blocking], "phone": phone_info,
            "recommended_template": OFFICIAL_VIDEO_TEMPLATE_SPEC}
