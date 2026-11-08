"""Phone number normalisation shared by importers, API and webhook."""

import re
from typing import Optional

import config


def digits_only(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def normalize_phone(raw: str, default_cc: Optional[str] = None) -> Optional[str]:
    """Return '+<digits>' (E.164) or None when the number is not usable.

    Handles Google-Contacts style annotations ("98765 43210 ::: 1"), national prefixes
    (0xxxxxxxxxx), international prefixes (00...) and bare 10-digit Indian mobiles.
    """
    if not raw:
        return None
    cc = (default_cc or config.DEFAULT_COUNTRY_CODE).strip()
    raw = raw.split(":::")[0].strip()
    has_plus = raw.startswith("+")
    d = digits_only(raw)
    if not d:
        return None
    if has_plus:
        pass
    elif d.startswith("00"):
        d = d[2:]
    elif cc == "91" and len(d) == 10 and d[0] in "6789":
        d = "91" + d
    elif d.startswith("0") and 10 < len(d) <= 12:
        d = cc + d.lstrip("0")
    elif len(d) == 10:
        d = cc + d
    if not (10 <= len(d) <= 15):
        return None
    if cc == "91" and d.startswith("91") and len(d) == 12 and d[2] not in "6789":
        return None  # not an Indian mobile number
    return "+" + d


def api_phone(phone: str) -> str:
    """Format for the Cloud API 'to' field: digits only, no '+'."""
    return digits_only(phone)
