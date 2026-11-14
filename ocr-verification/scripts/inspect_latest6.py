import json
import sqlite3
from pathlib import Path

ids = [
    "6af6fb1f14049abda792bf0a",
    "6af6fe5414049abda792bf1a",
    "6af6fee314049abda792bf28",
    "6af6ff5a14049abda792bf36",
    "6af7000414049abda792bf44",
    "6af7040b14049abda792bf52",
]
db = Path(__file__).resolve().parents[1] / "temp" / "verification_history.db"
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
for tid in ids:
    row = con.execute(
        "SELECT id, timestamp, decision, payload_json FROM verification_history WHERE transaction_id=? ORDER BY id DESC LIMIT 1",
        (tid,),
    ).fetchone()
    print("=" * 90)
    if not row:
        print(tid, "NO HISTORY")
        continue
    p = json.loads(row["payload_json"])
    print(tid, row["timestamp"], row["decision"], "|", p.get("message"))
    txn = p.get("transaction") or {}
    print(" txn", txn.get("amount"), txn.get("date"), "files", txn.get("file_ids"))
    for d in p.get("documents") or []:
        e = d.get("extraction") or {}
        f = e.get("fields") or {}
        print(
            " doc",
            e.get("document_type"),
            "amt",
            (f.get("amount") or {}).get("value"),
            (f.get("amount") or {}).get("source"),
            "date",
            (f.get("documentDate") or {}).get("value"),
            "build",
            (e.get("metadata") or {}).get("extractor_build"),
        )
        print("  warn:", (e.get("warnings") or [])[:6])
        raw = e.get("raw_text") or ""
        print("  raw:", raw[:900].replace("\n", " | "))
