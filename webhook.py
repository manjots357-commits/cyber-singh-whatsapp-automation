"""
Meta WhatsApp webhook: verification, signature check, delivery receipts, opt-out/opt-in keywords,
template status updates.  Mounted by app.py. These two routes are the only public endpoints.
"""

import hashlib
import hmac
import json
import logging
import re
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

import config
import database as db

log = logging.getLogger("whatsapp.webhook")
router = APIRouter()

OPT_OUT_WORDS = {"stop", "unsubscribe", "cancel", "end", "quit", "stopall", "optout", "opt-out",
                 "बंद", "रोको", "ਬੰਦ", "ਰੋਕੋ"}
OPT_IN_WORDS = {"start", "subscribe", "unstop", "yes subscribe", "ਸ਼ੁਰੂ"}


def verify_signature(raw: bytes, header: str) -> bool:
    if not config.META_APP_SECRET:
        return config.WEBHOOK_ALLOW_UNSIGNED
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(config.META_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


@router.get("/webhook")
def verify_webhook(request: Request):
    q = request.query_params
    token = config.WEBHOOK_VERIFY_TOKEN
    if (q.get("hub.mode") == "subscribe" and token
            and hmac.compare_digest(q.get("hub.verify_token", ""), token)):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Verification failed")


def _keyword(text: str) -> str:
    return re.sub(r"[^\w\sऀ-ॿ਀-੿-]", "", (text or "").strip().lower())


def _handle_message(msg: Dict[str, Any]) -> str:
    digits = re.sub(r"\D", "", msg.get("from", ""))
    if not digits:
        return "ignored"
    db.touch_inbound(digits)
    mtype = msg.get("type")
    text = ""
    if mtype == "text":
        text = (msg.get("text") or {}).get("body", "")
    elif mtype == "button":
        text = (msg.get("button") or {}).get("text", "") or (msg.get("button") or {}).get("payload", "")
    elif mtype == "interactive":
        inter = msg.get("interactive") or {}
        text = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
    kw = _keyword(text)
    if kw in OPT_OUT_WORDS:
        return "opt_out" if db.apply_inbound_opt_out(digits) else "opt_out_unknown_contact"
    if kw in OPT_IN_WORDS:
        return "opt_in" if db.apply_inbound_opt_in(digits) else "opt_in_unknown_contact"
    return "inbound"


def process_payload(payload: Dict[str, Any]) -> Dict[str, int]:
    stats = {"statuses": 0, "messages": 0, "opt_out": 0, "opt_in": 0, "templates": 0}
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            field, value = change.get("field"), change.get("value", {}) or {}
            if field == "messages":
                for st in value.get("statuses", []):
                    err = (st.get("errors") or [{}])[0]
                    ok = db.update_dispatch_from_webhook(
                        st.get("id", ""), st.get("status", ""), st.get("timestamp"),
                        error_code=str(err.get("code", "")), error=err.get("title") or err.get("message", ""))
                    stats["statuses"] += 1 if ok else 0
                for m in value.get("messages", []):
                    result = _handle_message(m)
                    stats["messages"] += 1
                    if result == "opt_out":
                        stats["opt_out"] += 1
                    elif result == "opt_in":
                        stats["opt_in"] += 1
            elif field == "message_template_status_update":
                event = (value.get("event") or "").upper()
                name = value.get("message_template_name")
                if name and event:
                    db.set_template_status(name, event, value.get("reason") or "",
                                           language=value.get("message_template_language"))
                    stats["templates"] += 1
    return stats


@router.post("/webhook")
async def receive_webhook(request: Request):
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    try:
        db.log_webhook_event(payload.get("object", "unknown"), payload)
        stats = process_payload(payload)
    except Exception:  # never make Meta retry forever because of our bug; the raw event is logged
        log.exception("webhook processing failed")
        stats = {"error": 1}
    return {"ok": True, **stats}


@router.get("/health")
def health():
    return Response(content="ok", media_type="text/plain")
