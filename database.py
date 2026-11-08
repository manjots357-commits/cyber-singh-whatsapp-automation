"""
Database layer (SQLite) for the WhatsApp Cloud API automation.

Backwards compatible with the first version of the project: existing tables are
migrated in place (columns are only ever ADDED, nothing is deleted).

Consent model
-------------
A contact may receive a campaign only when ALL of these hold:
    opted_in = 1            (they agreed to receive messages)
    consent_verified = 1    (the account owner recorded HOW they agreed)
    opted_out_at IS NULL    (they never replied STOP afterwards)
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv(
    "WA_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatsapp_automation.db"),
)

# Statuses that mean "WhatsApp really accepted this message"
REAL_SENT_STATUSES = ("SENT", "DELIVERED", "READ")
_STATUS_RANK = {"PENDING": 0, "SENT": 1, "DELIVERED": 2, "READ": 3}

AUDIENCE_GROUPS = [
    {"id": "vip_clients", "name": "VIP & Key Clients",
     "description": "High-priority clients and strategic partners receiving exclusive early video access.",
     "invite_link": "https://chat.whatsapp.com/sampleVIPGroup001", "color": "#10B981"},
    {"id": "new_leads", "name": "Inbound Leads & Prospects",
     "description": "Prospects who opted-in for product demos, product updates, and intro videos.",
     "invite_link": "https://chat.whatsapp.com/sampleLeadsGroup002", "color": "#3B82F6"},
    {"id": "community_members", "name": "Active Community Members",
     "description": "Engaged community participants and discussion leaders.",
     "invite_link": "https://chat.whatsapp.com/sampleCommunity003", "color": "#8B5CF6"},
    {"id": "event_attendees", "name": "Webinar & Workshop Attendees",
     "description": "Participants from past workshops, masterclasses, and Q&A sessions.",
     "invite_link": "https://chat.whatsapp.com/sampleEventGroup004", "color": "#F59E0B"},
    {"id": "content_subscribers", "name": "Video & Newsletter Subscribers",
     "description": "Audience members who subscribed specifically for weekly educational and video content.",
     "invite_link": "https://chat.whatsapp.com/sampleSubscribers005", "color": "#EC4899"},
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def _columns(cur, table: str) -> List[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [c[1] for c in cur.fetchall()]


def _add_column(cur, table: str, name: str, ddl: str):
    if name not in _columns(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db():
    """Create tables and run additive migrations."""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS audience_groups (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, invite_link TEXT, color TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    cur.execute("""CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, phone TEXT NOT NULL UNIQUE, email TEXT,
        group_id TEXT NOT NULL,
        opted_in INTEGER NOT NULL DEFAULT 0,
        opt_in_date TEXT, opt_in_channel TEXT, tags TEXT,
        is_test INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    _add_column(cur, "contacts", "is_test", "INTEGER DEFAULT 0")
    _add_column(cur, "contacts", "consent_verified", "INTEGER DEFAULT 0")
    _add_column(cur, "contacts", "consent_basis", "TEXT")
    _add_column(cur, "contacts", "consent_recorded_at", "TEXT")
    _add_column(cur, "contacts", "opted_out_at", "TEXT")
    _add_column(cur, "contacts", "last_inbound_at", "TEXT")

    cur.execute("""CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, video_url TEXT NOT NULL, message_template TEXT NOT NULL,
        target_groups TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'DRAFT',
        total_recipients INTEGER DEFAULT 0, sent_count INTEGER DEFAULT 0,
        failed_count INTEGER DEFAULT 0, skipped_count INTEGER DEFAULT 0,
        is_test_run INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, approved_at TEXT, completed_at TEXT)""")
    _add_column(cur, "campaigns", "is_test_run", "INTEGER DEFAULT 0")
    _add_column(cur, "campaigns", "template_name", "TEXT")
    _add_column(cur, "campaigns", "template_language", "TEXT")
    _add_column(cur, "campaigns", "template_params", "TEXT")
    _add_column(cur, "campaigns", "contact_ids", "TEXT")
    _add_column(cur, "campaigns", "expected_count", "INTEGER")
    _add_column(cur, "campaigns", "status_note", "TEXT")
    _add_column(cur, "campaigns", "started_at", "TEXT")

    cur.execute("""CREATE TABLE IF NOT EXISTS dispatches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER NOT NULL, contact_id INTEGER NOT NULL, phone TEXT NOT NULL,
        video_url TEXT NOT NULL, message_content TEXT NOT NULL,
        status TEXT NOT NULL, error_details TEXT, sent_at TEXT, wa_message_id TEXT,
        UNIQUE(campaign_id, contact_id))""")
    _add_column(cur, "dispatches", "error_code", "TEXT")
    _add_column(cur, "dispatches", "delivered_at", "TEXT")
    _add_column(cur, "dispatches", "read_at", "TEXT")
    _add_column(cur, "dispatches", "template_name", "TEXT")
    _add_column(cur, "dispatches", "mode", "TEXT")
    _add_column(cur, "dispatches", "updated_at", "TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_wa ON dispatches(wa_message_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_phone ON dispatches(phone, video_url)")

    cur.execute("""CREATE TABLE IF NOT EXISTS community_info (
        id INTEGER PRIMARY KEY, community_name TEXT NOT NULL, announcement_group_id TEXT,
        announcement_invite_link TEXT, description TEXT, guidelines TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    _add_column(cur, "community_info", "announcement_invite_link", "TEXT")

    cur.execute("""CREATE TABLE IF NOT EXISTS message_templates (
        name TEXT NOT NULL, language TEXT NOT NULL, category TEXT, status TEXT,
        body TEXT, header TEXT, footer TEXT, buttons TEXT, meta_id TEXT, rejected_reason TEXT,
        synced_at TEXT, PRIMARY KEY (name, language))""")

    cur.execute("""CREATE TABLE IF NOT EXISTS consent_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INTEGER NOT NULL, action TEXT NOT NULL,
        basis TEXT, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    cur.execute("""CREATE TABLE IF NOT EXISTS webhook_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT DEFAULT CURRENT_TIMESTAMP,
        kind TEXT, payload TEXT)""")

    cur.execute("""CREATE TABLE IF NOT EXISTS system_state (
        key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS live_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, wa_id TEXT UNIQUE, to_tail TEXT, template_name TEXT,
        status TEXT, error_code TEXT, error_details TEXT, created_at TEXT, delivered_at TEXT)""")

    for g in AUDIENCE_GROUPS:
        cur.execute("""INSERT OR IGNORE INTO audience_groups (id, name, description, invite_link, color)
                       VALUES (?,?,?,?,?)""", (g["id"], g["name"], g["description"], g["invite_link"], g["color"]))

    cur.execute("SELECT COUNT(*) FROM community_info")
    if cur.fetchone()[0] == 0:
        cur.execute("""INSERT INTO community_info
            (id, community_name, announcement_group_id, announcement_invite_link, description, guidelines)
            VALUES (1, 'Official Video Broadcast & Resource Hub', 'announcement_main',
            'https://chat.whatsapp.com/sampleAnnouncementChannel',
            'Central WhatsApp Community connecting all 5 audience interest groups for official video drops and announcements.',
            '1. Consented updates only. 2. No spam. 3. Video distribution respects member notification preferences.')""")

    # Correction of historic data: rows written by the old Safe-Mode simulator were stored as
    # SENT, which would later block the real send as a "duplicate". Re-label them (nothing deleted).
    cur.execute("""UPDATE dispatches SET status='SIMULATED'
                   WHERE status='SENT' AND wa_message_id LIKE 'SIM_MSG_%'""")

    conn.commit()
    conn.close()


# ---------------------------------------------------------------- groups / community
def get_all_groups() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT g.*,
               COUNT(c.id) AS total_contacts,
               SUM(CASE WHEN c.opted_in = 1 THEN 1 ELSE 0 END) AS opted_in_contacts,
               SUM(CASE WHEN c.opted_in = 0 THEN 1 ELSE 0 END) AS non_consented_contacts,
               SUM(CASE WHEN c.is_test = 1 THEN 1 ELSE 0 END) AS test_contacts,
               SUM(CASE WHEN c.opted_in = 1 AND c.consent_verified = 1 AND c.opted_out_at IS NULL
                         AND c.is_test = 0 THEN 1 ELSE 0 END) AS sendable_contacts
        FROM audience_groups g LEFT JOIN contacts c ON g.id = c.group_id
        GROUP BY g.id ORDER BY g.rowid ASC""").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_group_invite_link(group_id: str, new_link: str):
    conn = get_db_connection()
    conn.execute("UPDATE audience_groups SET invite_link = ? WHERE id = ?", (new_link.strip(), group_id.strip()))
    conn.commit(); conn.close()


def update_group_details(group_id: str, name: str, description: str, invite_link: str):
    conn = get_db_connection()
    conn.execute("UPDATE audience_groups SET name=?, description=?, invite_link=? WHERE id=?",
                 (name.strip(), description.strip(), invite_link.strip(), group_id.strip()))
    conn.commit(); conn.close()


def update_community_info(community_name: str, announcement_invite_link: str, description: str, guidelines: str):
    conn = get_db_connection()
    conn.execute("""UPDATE community_info SET community_name=?, announcement_invite_link=?, description=?,
                    guidelines=?, updated_at=CURRENT_TIMESTAMP WHERE id=1""",
                 (community_name.strip(), announcement_invite_link.strip(), description.strip(), guidelines.strip()))
    conn.commit(); conn.close()


def get_community_info():
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM community_info WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else {}


# ---------------------------------------------------------------- contacts
SENDABLE_SQL = "c.opted_in = 1 AND c.consent_verified = 1 AND c.opted_out_at IS NULL"


def is_sendable(contact: Dict[str, Any]) -> bool:
    return bool(contact.get("opted_in")) and bool(contact.get("consent_verified")) and not contact.get("opted_out_at")


def get_contacts(group_id: Optional[str] = None, only_opted_in: bool = True,
                 is_test: Optional[bool] = None, only_sendable: bool = False) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    q = """SELECT c.*, g.name AS group_name FROM contacts c
           JOIN audience_groups g ON c.group_id = g.id WHERE 1=1"""
    params: list = []
    if group_id:
        q += " AND c.group_id = ?"; params.append(group_id)
    if only_opted_in:
        q += " AND c.opted_in = 1"
    if only_sendable:
        q += f" AND {SENDABLE_SQL}"
    if is_test is not None:
        q += " AND c.is_test = ?"; params.append(1 if is_test else 0)
    q += " ORDER BY c.name ASC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_contacts_by_ids(ids: List[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    conn = get_db_connection()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"""SELECT c.*, g.name AS group_name FROM contacts c
                            JOIN audience_groups g ON c.group_id = g.id
                            WHERE c.id IN ({marks}) ORDER BY c.name ASC""", list(ids)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_contacts(q: str = "", status: str = "all", group_id: Optional[str] = None,
                    limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """status: all | sendable | unverified | opted_out"""
    conn = get_db_connection()
    where, params = ["c.is_test = 0"], []
    if q:
        where.append("(c.name LIKE ? OR c.phone LIKE ?)"); params += [f"%{q}%", f"%{q}%"]
    if group_id:
        where.append("c.group_id = ?"); params.append(group_id)
    if status == "sendable":
        where.append(SENDABLE_SQL)
    elif status == "unverified":
        where.append("(c.consent_verified = 0 OR c.opted_in = 0) AND c.opted_out_at IS NULL")
    elif status == "opted_out":
        where.append("c.opted_out_at IS NOT NULL")
    w = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM contacts c WHERE {w}", params).fetchone()[0]
    rows = conn.execute(f"""SELECT c.*, g.name AS group_name FROM contacts c
                            JOIN audience_groups g ON c.group_id = g.id WHERE {w}
                            ORDER BY c.name ASC LIMIT ? OFFSET ?""", params + [limit, offset]).fetchall()
    conn.close()
    return {"total": total, "contacts": [dict(r) for r in rows]}


def add_contact(name: str, phone: str, group_id: str, opted_in: bool = False, email: str = "",
                channel: str = "web_form", tags: str = "", is_test: bool = False,
                consent_basis: Optional[str] = None) -> int:
    """Insert/update a contact. Consent is NEVER silently granted:
    a contact only becomes sendable when consent_basis is supplied (or via set_consent)."""
    conn = get_db_connection()
    cur = conn.cursor()
    verified = 1 if (opted_in and consent_basis) else 0
    stamp = now_iso() if opted_in else None
    cur.execute("""
        INSERT INTO contacts (name, phone, email, group_id, opted_in, opt_in_date, opt_in_channel, tags,
                              is_test, consent_verified, consent_basis, consent_recorded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(phone) DO UPDATE SET
            name = excluded.name,
            email = CASE WHEN excluded.email != '' THEN excluded.email ELSE contacts.email END,
            group_id = excluded.group_id,
            tags = excluded.tags,
            is_test = excluded.is_test,
            opted_in = CASE WHEN contacts.opted_out_at IS NOT NULL THEN 0 ELSE
                            CASE WHEN excluded.opted_in = 1 THEN 1 ELSE contacts.opted_in END END,
            consent_verified = CASE WHEN contacts.opted_out_at IS NOT NULL THEN 0 ELSE
                            CASE WHEN excluded.consent_verified = 1 THEN 1 ELSE contacts.consent_verified END END,
            consent_basis = CASE WHEN excluded.consent_verified = 1 AND contacts.opted_out_at IS NULL
                            THEN excluded.consent_basis ELSE contacts.consent_basis END,
            opt_in_channel = CASE WHEN excluded.opted_in = 1 AND contacts.opted_out_at IS NULL
                            THEN excluded.opt_in_channel ELSE contacts.opt_in_channel END
    """, (name.strip(), phone.strip(), (email or "").strip(), group_id, 1 if opted_in else 0, stamp,
          channel, tags, 1 if is_test else 0, verified, consent_basis, stamp if verified else None))
    conn.commit()
    row = conn.execute("SELECT id FROM contacts WHERE phone = ?", (phone.strip(),)).fetchone()
    conn.close()
    return row["id"]


def find_contact_by_digits(digits: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("""SELECT * FROM contacts
        WHERE REPLACE(REPLACE(REPLACE(phone,'+',''),' ',''),'-','') = ? LIMIT 1""", (digits,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------- consent management
def _log_consent(cur, contact_id: int, action: str, basis: str = "", note: str = ""):
    cur.execute("INSERT INTO consent_log (contact_id, action, basis, note, created_at) VALUES (?,?,?,?,?)",
                (contact_id, action, basis, note, now_iso()))


def set_consent(contact_ids: List[int], basis: str, note: str = "") -> int:
    """Record that these contacts agreed to receive messages. Opted-out contacts are skipped:
    only the contact themselves (inbound START) can undo an opt-out."""
    conn = get_db_connection(); cur = conn.cursor(); n = 0
    for cid in contact_ids:
        row = cur.execute("SELECT opted_out_at FROM contacts WHERE id=?", (cid,)).fetchone()
        if not row or row["opted_out_at"]:
            continue
        cur.execute("""UPDATE contacts SET opted_in=1, consent_verified=1, consent_basis=?,
                       opt_in_date=COALESCE(opt_in_date, ?), consent_recorded_at=?, opt_in_channel=?
                       WHERE id=?""", (basis, now_iso(), now_iso(), "owner_attested:" + basis, cid))
        _log_consent(cur, cid, "OPT_IN_RECORDED", basis, note)
        n += 1
    conn.commit(); conn.close()
    return n


def revoke_consent(contact_ids: List[int], reason: str = "revoked_by_owner") -> int:
    conn = get_db_connection(); cur = conn.cursor(); n = 0
    for cid in contact_ids:
        cur.execute("""UPDATE contacts SET opted_in=0, consent_verified=0, opted_out_at=? WHERE id=?""",
                    (now_iso(), cid))
        _log_consent(cur, cid, "OPT_OUT", reason)
        n += cur.rowcount
    conn.commit(); conn.close()
    return n


def apply_inbound_opt_out(digits: str) -> bool:
    c = find_contact_by_digits(digits)
    if not c:
        return False
    revoke_consent([c["id"]], reason="inbound_stop_keyword")
    return True


def apply_inbound_opt_in(digits: str) -> bool:
    """The contact themselves wrote START/SUBSCRIBE to our number: a first-party opt-in."""
    c = find_contact_by_digits(digits)
    if not c:
        return False
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""UPDATE contacts SET opted_in=1, consent_verified=1, opted_out_at=NULL,
                   consent_basis='inbound_message_start', consent_recorded_at=?,
                   opt_in_date=COALESCE(opt_in_date, ?), opt_in_channel='whatsapp_inbound_start'
                   WHERE id=?""", (now_iso(), now_iso(), c["id"]))
    _log_consent(cur, c["id"], "OPT_IN_SELF", "inbound_message_start")
    conn.commit(); conn.close()
    return True


def touch_inbound(digits: str):
    """Remember when the contact last wrote to us (opens the 24h free-text window)."""
    conn = get_db_connection()
    conn.execute("""UPDATE contacts SET last_inbound_at=?
        WHERE REPLACE(REPLACE(REPLACE(phone,'+',''),' ',''),'-','') = ?""", (now_iso(), digits))
    conn.commit(); conn.close()


def within_service_window(contact: Dict[str, Any]) -> bool:
    ts = contact.get("last_inbound_at")
    if not ts:
        return False
    try:
        return datetime.now() - datetime.fromisoformat(ts) < timedelta(hours=24)
    except ValueError:
        return False


def consent_summary() -> Dict[str, int]:
    conn = get_db_connection()
    r = conn.execute(f"""SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN c.is_test=0 THEN 1 ELSE 0 END) AS real_total,
        SUM(CASE WHEN c.is_test=0 AND {SENDABLE_SQL} THEN 1 ELSE 0 END) AS sendable,
        SUM(CASE WHEN c.is_test=0 AND c.opted_out_at IS NULL AND (c.opted_in=0 OR c.consent_verified=0)
                 THEN 1 ELSE 0 END) AS unverified,
        SUM(CASE WHEN c.is_test=0 AND c.opted_out_at IS NOT NULL THEN 1 ELSE 0 END) AS opted_out
        FROM contacts c""").fetchone()
    conn.close()
    return {k: (r[k] or 0) for k in r.keys()}


# ---------------------------------------------------------------- campaigns
def create_campaign(title: str, video_url: str, message_template: str, target_groups: List[str],
                    is_test_run: bool = False, template_name: Optional[str] = None,
                    template_language: Optional[str] = None, template_params: Optional[List[str]] = None,
                    contact_ids: Optional[List[int]] = None, expected_count: Optional[int] = None) -> int:
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""INSERT INTO campaigns (title, video_url, message_template, target_groups, status, is_test_run,
                   template_name, template_language, template_params, contact_ids, expected_count)
                   VALUES (?,?,?,?, 'DRAFT', ?,?,?,?,?,?)""",
                (title.strip(), video_url.strip(), message_template.strip(), ",".join(target_groups),
                 1 if is_test_run else 0, template_name, template_language,
                 json.dumps(template_params) if template_params else None,
                 json.dumps(contact_ids) if contact_ids else None, expected_count))
    conn.commit(); cid = cur.lastrowid; conn.close()
    return cid


def get_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_campaign_fields(campaign_id: int, **fields):
    if not fields:
        return
    conn = get_db_connection()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE campaigns SET {sets} WHERE id=?", list(fields.values()) + [campaign_id])
    conn.commit(); conn.close()


def update_campaign_status(campaign_id: int, status: str, total: int = 0, sent: int = 0,
                           failed: int = 0, skipped: int = 0, note: Optional[str] = None):
    conn = get_db_connection(); now = now_iso()
    if status == "APPROVED":
        conn.execute("UPDATE campaigns SET status=?, approved_at=? WHERE id=?", (status, now, campaign_id))
    elif status in ("COMPLETED", "PARTIAL", "PAUSED", "FAILED"):
        conn.execute("""UPDATE campaigns SET status=?, total_recipients=?, sent_count=?, failed_count=?,
                        skipped_count=?, completed_at=?, status_note=COALESCE(?, status_note) WHERE id=?""",
                     (status, total, sent, failed, skipped, now, note, campaign_id))
    else:
        conn.execute("UPDATE campaigns SET status=?, status_note=COALESCE(?, status_note) WHERE id=?",
                     (status, note, campaign_id))
    conn.commit(); conn.close()


def get_campaigns():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM campaigns ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_campaign_details(campaign_id: int):
    conn = get_db_connection()
    camp = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not camp:
        conn.close(); return None
    rows = conn.execute("""SELECT d.*, c.name AS contact_name, g.name AS group_name
        FROM dispatches d JOIN contacts c ON d.contact_id = c.id
        JOIN audience_groups g ON c.group_id = g.id
        WHERE d.campaign_id=? ORDER BY d.id ASC""", (campaign_id,)).fetchall()
    stats = conn.execute("""SELECT status, COUNT(*) n FROM dispatches WHERE campaign_id=? GROUP BY status""",
                         (campaign_id,)).fetchall()
    conn.close()
    return {"campaign": dict(camp), "dispatches": [dict(r) for r in rows],
            "progress": {r["status"]: r["n"] for r in stats}}


def recover_interrupted_campaigns() -> int:
    """If the app was closed mid-send, mark the campaign INTERRUPTED so it can be resumed."""
    conn = get_db_connection()
    cur = conn.execute("""UPDATE campaigns SET status='INTERRUPTED',
        status_note='App stopped while sending. Review and resume.' WHERE status='IN_PROGRESS'""")
    conn.commit(); n = cur.rowcount; conn.close()
    return n


# ---------------------------------------------------------------- dispatches
def check_prior_video_send(phone: str, video_url: str) -> bool:
    """True when this phone already really received this exact URL (simulations don't count)."""
    conn = get_db_connection()
    marks = ",".join("?" * len(REAL_SENT_STATUSES))
    n = conn.execute(f"SELECT COUNT(*) FROM dispatches WHERE phone=? AND video_url=? AND status IN ({marks})",
                     (phone, video_url.strip(), *REAL_SENT_STATUSES)).fetchone()[0]
    conn.close()
    return n > 0


def record_dispatch(campaign_id: int, contact_id: int, phone: str, video_url: str, message: str,
                    status: str, error: str = "", wa_id: str = "", error_code: str = "",
                    template_name: Optional[str] = None, mode: Optional[str] = None):
    conn = get_db_connection()
    sent_at = now_iso() if status in REAL_SENT_STATUSES or status == "SIMULATED" else None
    conn.execute("""
        INSERT INTO dispatches (campaign_id, contact_id, phone, video_url, message_content, status,
                                error_details, sent_at, wa_message_id, error_code, template_name, mode, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(campaign_id, contact_id) DO UPDATE SET
            phone=excluded.phone, video_url=excluded.video_url, message_content=excluded.message_content,
            status=excluded.status, error_details=excluded.error_details,
            sent_at=COALESCE(excluded.sent_at, dispatches.sent_at),
            wa_message_id=CASE WHEN excluded.wa_message_id != '' THEN excluded.wa_message_id
                               ELSE dispatches.wa_message_id END,
            error_code=excluded.error_code, template_name=excluded.template_name, mode=excluded.mode,
            updated_at=excluded.updated_at""",
        (campaign_id, contact_id, phone, video_url, message, status, error, sent_at, wa_id,
         error_code, template_name, mode, now_iso()))
    conn.commit(); conn.close()


def get_pending_dispatches(campaign_id: int) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("""SELECT d.*, c.name AS contact_name, c.opted_in, c.consent_verified, c.opted_out_at,
                                  c.last_inbound_at, g.name AS group_name
        FROM dispatches d JOIN contacts c ON c.id = d.contact_id JOIN audience_groups g ON g.id = c.group_id
        WHERE d.campaign_id=? AND d.status='PENDING' ORDER BY d.id ASC""", (campaign_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_dispatch_from_webhook(wa_id: str, status: str, ts: Optional[str],
                                 error_code: str = "", error: str = "") -> bool:
    """status: sent | delivered | read | failed. Never moves a message backwards."""
    conn = get_db_connection()
    row = conn.execute("SELECT id, status FROM dispatches WHERE wa_message_id=?", (wa_id,)).fetchone()
    if not row:
        conn.close()
        return update_live_test_from_webhook(wa_id, status, ts, error_code, error)
    stamp = ts or now_iso()
    if status == "failed":
        conn.execute("""UPDATE dispatches SET status='FAILED', error_code=?, error_details=?, updated_at=?
                        WHERE id=?""", (error_code, error, now_iso(), row["id"]))
    else:
        new = status.upper()
        if _STATUS_RANK.get(new, -1) > _STATUS_RANK.get(row["status"], -1):
            col = {"DELIVERED": "delivered_at", "READ": "read_at"}.get(new)
            extra = f", {col}=?" if col else ""
            params = [new, now_iso()] + ([stamp] if col else []) + [row["id"]]
            conn.execute(f"UPDATE dispatches SET status=?, updated_at=?{extra} WHERE id=?", params)
            if new == "READ":
                conn.execute("UPDATE dispatches SET delivered_at=COALESCE(delivered_at, ?) WHERE id=?",
                             (stamp, row["id"]))
    conn.commit(); conn.close()
    return True


def count_unique_recipients_24h() -> int:
    since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    conn = get_db_connection()
    marks = ",".join("?" * len(REAL_SENT_STATUSES))
    n = conn.execute(f"""SELECT COUNT(DISTINCT phone) FROM dispatches
                         WHERE status IN ({marks}) AND sent_at >= ?""", (*REAL_SENT_STATUSES, since)).fetchone()[0]
    conn.close()
    return n


# ---------------------------------------------------------------- live-sending gate state
def get_state(key: str) -> Optional[str]:
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row and row["value"] else None


def set_state(key: str, value: Optional[str]):
    conn = get_db_connection()
    conn.execute("""INSERT INTO system_state (key, value, updated_at) VALUES (?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                 (key, value, now_iso()))
    conn.commit(); conn.close()


def record_live_test(wa_id: str, to_phone: str, template_name: str):
    conn = get_db_connection()
    conn.execute("""INSERT OR IGNORE INTO live_tests (wa_id, to_tail, template_name, status, created_at)
                    VALUES (?,?,?,?,?)""", (wa_id, (to_phone or "")[-4:], template_name, "sent", now_iso()))
    conn.commit(); conn.close()


def update_live_test_from_webhook(wa_id: str, status: str, ts: Optional[str], error_code: str = "",
                                  error: str = "") -> bool:
    conn = get_db_connection()
    row = conn.execute("SELECT id, status FROM live_tests WHERE wa_id=?", (wa_id,)).fetchone()
    if not row:
        conn.close(); return False
    new = status.lower()
    order = {"sent": 1, "delivered": 2, "read": 3}
    if new == "failed":
        conn.execute("UPDATE live_tests SET status='failed', error_code=?, error_details=? WHERE id=?",
                     (error_code, error, row["id"]))
    elif order.get(new, 0) > order.get(row["status"], 0):
        conn.execute("UPDATE live_tests SET status=?, delivered_at=COALESCE(delivered_at, ?) WHERE id=?",
                     (new, now_iso() if new in ("delivered", "read") else None, row["id"]))
    conn.commit(); conn.close()
    return True


def latest_live_test() -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM live_tests ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def has_delivered_live_test() -> bool:
    conn = get_db_connection()
    n = conn.execute("SELECT COUNT(*) FROM live_tests WHERE status IN ('delivered','read')").fetchone()[0]
    conn.close()
    return n > 0


# ---------------------------------------------------------------- templates / webhook log
def upsert_template(name: str, language: str, category: str = "", status: str = "", body: str = "",
                    header: str = "", footer: str = "", buttons: str = "", meta_id: str = "",
                    rejected_reason: str = ""):
    conn = get_db_connection()
    conn.execute("""INSERT INTO message_templates (name, language, category, status, body, header, footer,
                    buttons, meta_id, rejected_reason, synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(name, language) DO UPDATE SET category=excluded.category, status=excluded.status,
          body=COALESCE(NULLIF(excluded.body,''), message_templates.body),
          header=COALESCE(NULLIF(excluded.header,''), message_templates.header),
          footer=COALESCE(NULLIF(excluded.footer,''), message_templates.footer),
          buttons=COALESCE(NULLIF(excluded.buttons,''), message_templates.buttons),
          meta_id=COALESCE(NULLIF(excluded.meta_id,''), message_templates.meta_id),
          rejected_reason=excluded.rejected_reason, synced_at=excluded.synced_at""",
                 (name, language, category, status, body, header, footer, buttons, meta_id,
                  rejected_reason, now_iso()))
    conn.commit(); conn.close()


def set_template_status(name: str, status: str, reason: str = "", language: Optional[str] = None):
    conn = get_db_connection()
    if language:
        conn.execute("UPDATE message_templates SET status=?, rejected_reason=?, synced_at=? WHERE name=? AND language=?",
                     (status, reason, now_iso(), name, language))
    else:
        conn.execute("UPDATE message_templates SET status=?, rejected_reason=?, synced_at=? WHERE name=?",
                     (status, reason, now_iso(), name))
    conn.commit(); conn.close()


def list_templates() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM message_templates ORDER BY name, language").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_template(name: str, language: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    if language:
        row = conn.execute("SELECT * FROM message_templates WHERE name=? AND language=?", (name, language)).fetchone()
    else:
        row = conn.execute("SELECT * FROM message_templates WHERE name=? ORDER BY synced_at DESC", (name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def log_webhook_event(kind: str, payload: Any):
    conn = get_db_connection()
    conn.execute("INSERT INTO webhook_events (kind, payload) VALUES (?,?)", (kind, json.dumps(payload)[:20000]))
    conn.execute("DELETE FROM webhook_events WHERE id < (SELECT MAX(id) FROM webhook_events) - 2000")
    conn.commit(); conn.close()


# ---------------------------------------------------------------- analytics
def analytics() -> Dict[str, Any]:
    conn = get_db_connection()
    status_rows = conn.execute("SELECT status, COUNT(*) n FROM dispatches GROUP BY status").fetchall()
    by_status = {r["status"]: r["n"] for r in status_rows}
    sent_total = sum(by_status.get(s, 0) for s in REAL_SENT_STATUSES)
    delivered = by_status.get("DELIVERED", 0) + by_status.get("READ", 0)
    read = by_status.get("READ", 0)
    errors = conn.execute("""SELECT COALESCE(NULLIF(error_code,''),'unknown') code, COUNT(*) n,
                             MAX(error_details) sample FROM dispatches WHERE status='FAILED'
                             GROUP BY code ORDER BY n DESC LIMIT 10""").fetchall()
    daily = conn.execute("""SELECT substr(sent_at,1,10) day, COUNT(*) n FROM dispatches
                            WHERE status IN ('SENT','DELIVERED','READ') AND sent_at IS NOT NULL
                            GROUP BY day ORDER BY day DESC LIMIT 14""").fetchall()
    per_campaign = conn.execute("""SELECT c.id, c.title, c.status, c.created_at,
        SUM(CASE WHEN d.status IN ('SENT','DELIVERED','READ') THEN 1 ELSE 0 END) sent,
        SUM(CASE WHEN d.status IN ('DELIVERED','READ') THEN 1 ELSE 0 END) delivered,
        SUM(CASE WHEN d.status='READ' THEN 1 ELSE 0 END) read,
        SUM(CASE WHEN d.status='FAILED' THEN 1 ELSE 0 END) failed,
        SUM(CASE WHEN d.status='SIMULATED' THEN 1 ELSE 0 END) simulated,
        SUM(CASE WHEN d.status='PENDING' THEN 1 ELSE 0 END) pending
        FROM campaigns c LEFT JOIN dispatches d ON d.campaign_id = c.id
        GROUP BY c.id ORDER BY c.id DESC LIMIT 20""").fetchall()
    optouts = conn.execute("SELECT COUNT(*) FROM consent_log WHERE action='OPT_OUT'").fetchone()[0]
    conn.close()
    return {
        "by_status": by_status,
        "sent_total": sent_total,
        "delivery_rate": round(delivered / sent_total * 100, 1) if sent_total else 0,
        "read_rate": round(read / sent_total * 100, 1) if sent_total else 0,
        "opt_outs": optouts,
        "top_errors": [dict(e) for e in errors],
        "daily": [dict(d) for d in reversed(daily)],
        "campaigns": [dict(c) for c in per_campaign],
        "consent": consent_summary(),
        "unique_recipients_24h": count_unique_recipients_24h(),
    }


# ---------------------------------------------------------------- test-data helper (legacy)
def seed_isolated_dummy_contacts():
    """Dummy contacts, flagged is_test=1, for safe dry runs. Never sent to a real number."""
    dummy = [
        ("Dummy Test Alpha (VIP)", "+15550109901", "vip_clients", True, "TEST_DUMMY"),
        ("Dummy Test Beta (Leads)", "+15550109902", "new_leads", True, "TEST_DUMMY"),
        ("Dummy Test Gamma (Community)", "+15550109903", "community_members", True, "TEST_DUMMY"),
        ("Dummy Test Delta (Events)", "+15550109904", "event_attendees", True, "TEST_DUMMY"),
        ("Dummy Test Epsilon (Subscribers)", "+15550109905", "content_subscribers", True, "TEST_DUMMY"),
        ("Dummy Unconsented (Control)", "+15550109906", "vip_clients", False, "TEST_NO_CONSENT"),
    ]
    for name, phone, gid, opted, tag in dummy:
        add_contact(name, phone, gid, opted_in=opted, tags=tag, channel="test_simulator", is_test=True,
                    consent_basis="test_fixture" if opted else None)
