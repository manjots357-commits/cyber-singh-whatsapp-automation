"""Export people who are READY to message (opt-in recorded, not opted out) to contacts.csv (name,phone)."""
import csv
from pathlib import Path

import database as db

db.init_db()
conn = db.get_db_connection()
rows = conn.execute(
    "SELECT name, phone FROM contacts c WHERE c.is_test=0 AND " + db.SENDABLE_SQL + " ORDER BY c.id"
).fetchall()
with open(Path(__file__).parent / "contacts.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["name", "phone"])
    w.writerows([(r["name"], r["phone"]) for r in rows])
print(f"Exported {len(rows)} contacts with confirmed opt-in -> contacts.csv")
