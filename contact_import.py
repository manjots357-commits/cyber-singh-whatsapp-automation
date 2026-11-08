"""
Contact import (Google Contacts CSV or a simple name,phone CSV).

IMPORTANT: importing a contact NEVER grants consent. Every imported number starts as
"not opted in". Consent is recorded afterwards, per person or per selection, in the dashboard
(Contacts -> "Record opt-in"), or by the contact themselves replying START to your number.
Existing contacts are left untouched (group, consent and opt-out state are preserved).
"""

import csv
import io
import sys
from typing import Any, Dict, List

import database as db
from phone_utils import digits_only, normalize_phone

NAME_COLS = ("Name", "name", "Full Name")
PHONE_COLS = ("Phone 1 - Value", "Phone 2 - Value", "Phone 3 - Value", "Phone", "phone", "Mobile",
              "mobile", "Number", "number")


def _row_phones(row: Dict[str, str]) -> List[str]:
    found: List[str] = []
    for col in PHONE_COLS:
        val = (row.get(col) or "").strip()
        if val:
            found.extend(p.strip() for p in val.split(":::") if p.strip())
    return found


def _row_name(row: Dict[str, str]) -> str:
    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    name = f"{first} {last}".strip()
    for col in NAME_COLS:
        if not name:
            name = (row.get(col) or "").strip()
    return name or (row.get("Organization Name") or "").strip() or "Contact"


def import_csv_text(text: str, group_id: str = "content_subscribers", source: str = "csv_import") -> Dict[str, Any]:
    db.init_db()
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    stats = {"imported": 0, "already_exists": 0, "invalid": 0, "duplicate_in_file": 0, "rows": 0}
    seen = set()
    for row in reader:
        stats["rows"] += 1
        phone = next((p for p in (normalize_phone(x) for x in _row_phones(row)) if p), None)
        if not phone:
            stats["invalid"] += 1
            continue
        if phone in seen:
            stats["duplicate_in_file"] += 1
            continue
        seen.add(phone)
        if db.find_contact_by_digits(digits_only(phone)):
            stats["already_exists"] += 1
            continue
        db.add_contact(_row_name(row), phone, group_id, opted_in=False,
                       email=(row.get("E-mail 1 - Value") or row.get("Email") or row.get("email") or ""),
                       channel=source, tags="IMPORTED_UNVERIFIED")
        stats["imported"] += 1
    return stats


def import_csv_file(path: str, group_id: str = "content_subscribers") -> Dict[str, Any]:
    with open(path, encoding="utf-8", errors="ignore") as f:
        return import_csv_text(f.read(), group_id, source="csv_import")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_contacts.py contacts.csv [group_id]")
        sys.exit(1)
    result = import_csv_file(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "content_subscribers")
    print("Import finished (all contacts are 'not opted in' until you record consent):", result)
