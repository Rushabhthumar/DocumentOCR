"""Offline verify failing live PDFs using fresh extract + compare."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import EXTRACTOR_BUILD
from app.config import get_settings
from app.models.document_models import DocumentType
from app.models.extraction_models import ExtractionResult, FieldValue
from app.models.verification_models import TransactionSnapshot
from app.services.extraction_service import ExtractionService
from app.services.transaction_verification_service import TransactionVerificationService

CASES = [
    ("11656", Decimal("7790.00"), "2026-03-31T18:30:00Z"),
    ("11657", Decimal("13465.00"), "2026-04-17T18:30:00Z"),
    ("11659", Decimal("15480.00"), "2026-04-05T18:30:00Z"),
    ("11663", Decimal("3903.00"), "2026-06-01T18:30:00Z"),
    ("11660", Decimal("112000.00"), "2026-04-29T18:30:00Z"),
]


def main() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    extract = ExtractionService(settings=settings)
    tvs = TransactionVerificationService(settings=settings)
    print("build", EXTRACTOR_BUILD)

    for fid, amount, txn_date in CASES:
        path = ROOT / "temp" / f"live_{fid}.pdf"
        print("=" * 70, fid, "expected", amount, txn_date)
        extraction = extract.extract_file(path, file_id=fid, filename=path.name)
        fields = {k: v.value for k, v in extraction.fields.items()}
        print(
            " extracted amount",
            fields.get("amount"),
            "date",
            fields.get("documentDate"),
            "type",
            extraction.document_type,
        )
        snap = TransactionSnapshot(
            transaction_id=f"offline-{fid}",
            date=txn_date,
            amount=amount,
            file_ids=[fid],
        )
        comparisons, decision, warnings = tvs._compare_all(snap, [extraction])
        print(" decision", decision)
        for c in comparisons:
            print(" ", c.field_name, c.status, "exp", c.expected, "got", c.extracted)
        for w in warnings[:8]:
            print("  warn:", w)


if __name__ == "__main__":
    main()
