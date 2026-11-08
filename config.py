"""
Configuration for the WhatsApp Cloud API automation.

All secrets come from the environment / .env file. Nothing here is ever logged.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("true", "1", "yes")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


# ---- Meta WhatsApp Cloud API ---------------------------------------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip()
# Graph API v26.0 is the newest version at time of writing (v21.0 expires Jan 2027).
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v26.0").strip()

# ---- Webhook ----------------------------------------------------------------
# Token YOU choose; paste the same value into Meta -> WhatsApp -> Configuration.
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "").strip()
# Meta App Secret (App settings -> Basic). Used to verify X-Hub-Signature-256.
META_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
# Only for local experiments. Never enable in production.
WEBHOOK_ALLOW_UNSIGNED = _bool("WEBHOOK_ALLOW_UNSIGNED", "false")

# ---- Dashboard protection ---------------------------------------------------
# When set, every dashboard/API route (except /webhook and /health) requires HTTP Basic auth.
# When NOT set, the dashboard only answers requests coming from this computer.
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin").strip()
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()

# ---- Safety -----------------------------------------------------------------
# Default is SAFE (simulate only). Set SAFE_MODE=false deliberately to send for real.
FORCE_SAFE_MODE = _bool("SAFE_MODE", "true")

# Meta limits how many UNIQUE people you may start conversations with per rolling 24h.
# Unverified business = 250. Raise only after Meta raises your tier.
DAILY_RECIPIENT_LIMIT = _int("DAILY_RECIPIENT_LIMIT", 250)

SEND_DELAY_SECONDS = _float("SEND_DELAY_SECONDS", 0.3)
MAX_RETRIES = _int("MAX_RETRIES", 3)
# After this many consecutive unexplained failures the campaign pauses itself.
FAILURE_CIRCUIT_BREAKER = _int("FAILURE_CIRCUIT_BREAKER", 5)

# The one number the dashboard's "send test to myself" button may message (digits, with country code).
OWNER_WHATSAPP_NUMBER = os.getenv("OWNER_WHATSAPP_NUMBER", "").strip()

DEFAULT_COUNTRY_CODE = os.getenv("DEFAULT_COUNTRY_CODE", "91").strip()
DEFAULT_TEMPLATE_LANGUAGE = os.getenv("DEFAULT_TEMPLATE_LANGUAGE", "pa").strip()


def is_cloud_api_configured() -> bool:
    """True only when credentials exist AND safe mode is explicitly off."""
    return bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID and not FORCE_SAFE_MODE)


def has_credentials() -> bool:
    return bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID)


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


# Free-text template, only usable inside Meta's 24h customer-service window.
DEFAULT_TEMPLATE = """Hello {{name}}! 👋

Here is our latest update:
🎬 *{{video_title}}*
🔗 {{video_url}}

Reply STOP if you wish to opt-out at any time."""
