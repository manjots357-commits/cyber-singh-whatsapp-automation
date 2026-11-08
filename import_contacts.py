"""Command-line entry point kept for compatibility. Usage: python import_contacts.py contacts.csv"""
from contact_import import import_csv_file
import sys

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_contacts.py contacts.csv [group_id]")
        sys.exit(1)
    print(import_csv_file(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "content_subscribers"))
