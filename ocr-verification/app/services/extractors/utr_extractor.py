from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from app.models.extraction_models import FieldValue, PaymentRow
from app.validators.amount_validator import normalize_amount
from app.validators.date_validator import normalize_date
from app.validators.description_validator import compare_descriptions


HEADER_RE = re.compile(
    r"Pymt_Mode\s+Debit_Acct_no\s+Beneficiary Name\s+Beneficiary Account No\s+"
    r"Bene_IFSC_Code\s+Amount\s+Pymt_Date\s+Remark\s+STATUS\s+UTR\s*NO",
    re.IGNORECASE,
)
ROW_START_RE = re.compile(r"^(NEFT|RTGS|IMPS|FT)\b", re.IGNORECASE)
UTR_RE = re.compile(r"\b([A-Z]{2}\d{10,})\b")
DATE_RE = re.compile(r"\b(\d{2}[-/]\d{2}[-/]\d{2,4})\b")
IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
STATUS_RE = re.compile(r"\b(Success|Failed|Pending|Rejected)\b", re.IGNORECASE)


def _split_logical_lines(text: str) -> list[str]:
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(raw_lines) >= 2:
        return raw_lines

    compact = re.sub(r"[ \t]+", " ", text)
    compact = HEADER_RE.sub("\n", compact)
    parts = re.split(r"(?=(?:NEFT|RTGS|IMPS)\s+\d+)", compact, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def merge_wrapped_rows(lines: list[str]) -> list[str]:
    if not lines:
        return []
    filtered = []
    for line in lines:
        if HEADER_RE.search(line) or line.lower().startswith("pymt_mode"):
            continue
        filtered.append(line)

    merged: list[str] = []
    for line in filtered:
        if ROW_START_RE.match(line):
            merged.append(line)
        elif merged:
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)
    return merged


def _parse_row_blob(blob: str, page_number: int = 1) -> Optional[PaymentRow]:
    text = re.sub(r"\s+", " ", blob).strip()
    if not text:
        return None

    mode_match = re.match(r"^(NEFT|RTGS|IMPS|FT)\s+(\d+)\s+(.*)$", text, re.IGNORECASE)
    if not mode_match:
        return None

    mode = mode_match.group(1).upper()
    debit = mode_match.group(2)
    rest = mode_match.group(3).strip()

    utr_match = UTR_RE.search(rest)
    status_match = STATUS_RE.search(rest)
    date_match = DATE_RE.search(rest)
    ifsc_match = IFSC_RE.search(rest)

    utr = utr_match.group(1) if utr_match else None
    status = status_match.group(1) if status_match else None
    payment_date = date_match.group(1) if date_match else None
    ifsc = ifsc_match.group(1) if ifsc_match else None

    # Amount is the numeric token immediately before the payment date.
    amount = None
    amount_end = None
    if date_match:
        before_date = rest[: date_match.start()].rstrip()
        amount_match = re.search(r"(\d+(?:\.\d{1,2})?)\s*$", before_date)
        if amount_match:
            amount = normalize_amount(amount_match.group(1))
            amount_end = amount_match.start()

    # Beneficiary account is the long digit token immediately before IFSC.
    beneficiary_account = None
    account_start = None
    if ifsc_match:
        before_ifsc = rest[: ifsc_match.start()].rstrip()
        acct_match = re.search(r"(\d{9,18})\s*$", before_ifsc)
        if acct_match:
            beneficiary_account = acct_match.group(1)
            account_start = acct_match.start()

    # Beneficiary name sits between debit account section start and account number.
    if account_start is not None:
        beneficiary_name = rest[:account_start].strip(" -")
    elif ifsc_match:
        beneficiary_name = rest[: ifsc_match.start()].strip(" -")
    elif amount_end is not None:
        beneficiary_name = rest[:amount_end].strip(" -")
    else:
        beneficiary_name = rest

    beneficiary_name = re.sub(r"\s+", " ", beneficiary_name).strip() or None

    # Remark is between date and status.
    remark = None
    if date_match:
        after_date = rest[date_match.end() :].strip()
        if status_match and status_match.start() > date_match.end():
            remark = rest[date_match.end() : status_match.start()].strip(" -")
        elif utr_match and utr_match.start() > date_match.end():
            remark = rest[date_match.end() : utr_match.start()].strip(" -")
        else:
            remark = after_date
        if status:
            remark = re.sub(re.escape(status), " ", remark or "", flags=re.IGNORECASE)
        if utr:
            remark = (remark or "").replace(utr, " ")
        remark = re.sub(r"\s+", " ", remark or "").strip(" -") or None

    confidence = 0.5
    if amount and payment_date and utr and beneficiary_name:
        confidence = 0.95
    elif amount and payment_date and beneficiary_name:
        confidence = 0.85

    return PaymentRow(
        payment_mode=mode,
        debit_account_no=debit,
        beneficiary_name=beneficiary_name,
        beneficiary_account_no=beneficiary_account,
        bene_ifsc_code=ifsc,
        amount=amount,
        payment_date=payment_date,
        remark=remark,
        status=status,
        utr_no=utr,
        confidence=confidence,
        page_number=page_number,
        source_lines=[blob],
    )


def extract_payment_rows(text: str, page_number: int = 1) -> list[PaymentRow]:
    lines = _split_logical_lines(text)
    blobs = merge_wrapped_rows(lines)
    rows: list[PaymentRow] = []
    for blob in blobs:
        row = _parse_row_blob(blob, page_number=page_number)
        if row:
            rows.append(row)
    return rows


def _payment_date_matches_txn(payment_date: Optional[str], txn_date: Optional[str]) -> bool:
    if not payment_date or not txn_date:
        return False
    nd = normalize_date(txn_date)
    rd = normalize_date(payment_date)
    return bool(nd and rd and nd == rd)


def _score_remark_vs_narration(
    remark: Optional[str],
    narration: Optional[str],
    *,
    fuzzy_threshold: int = 85,
) -> tuple[bool, bool, float, str]:
    """
    Score document Remark against transaction narration.

    Returns (eligible, hard_match, score, explanation).
    hard_match = exact / token containment from compare_descriptions.
    eligible = hard_match or strong fuzzy (>= threshold).
    """
    cmp = compare_descriptions(
        narration,
        remark,
        fuzzy_threshold=fuzzy_threshold,
        field_name="remark",
    )
    score = float(cmp.score) if cmp.score is not None else 0.0
    if cmp.matched:
        return True, True, score or 100.0, cmp.explanation
    if score >= fuzzy_threshold:
        return True, False, score, cmp.explanation
    return False, False, score, cmp.explanation or "Remark did not match narration"


def select_matching_payment_row(
    rows: list[PaymentRow],
    *,
    amount: Optional[Decimal],
    txn_date: Optional[str],
    narration: Optional[str] = None,
    utr_candidates: Optional[list[str]] = None,
) -> tuple[Optional[PaymentRow], list[str]]:
    """
    Match one UTR payment row to the transaction.

    Does NOT sum row amounts. Filters rows by transaction date, then requires a
    unique match on date + amount + remark/narration.
    """
    warnings: list[str] = []
    if not rows:
        return None, ["No payment rows extracted"]

    if not txn_date:
        return None, ["Transaction date missing; cannot match UTR payment row by date"]

    date_hits = [r for r in rows if _payment_date_matches_txn(r.payment_date, txn_date)]
    if not date_hits:
        available = sorted({(r.payment_date or "missing") for r in rows})
        return None, [
            f"No payment row on transaction date {txn_date}; document dates: {available}"
        ]

    utr_candidates = [u.upper() for u in (utr_candidates or []) if u]
    candidates = date_hits
    if utr_candidates:
        utr_hits = [
            r for r in date_hits if r.utr_no and r.utr_no.upper() in utr_candidates
        ]
        if utr_hits:
            candidates = utr_hits
        else:
            warnings.append(
                "UTR candidates present but none matched a payment row on transaction date"
            )

    if amount is None:
        return None, warnings + [
            "Transaction amount missing; cannot match UTR payment row by amount"
        ]

    amount_hits = [
        r
        for r in candidates
        if r.amount is not None and abs(r.amount - amount) <= Decimal("0.01")
    ]
    if not amount_hits:
        return None, warnings + [
            f"No payment row on transaction date matched amount {amount}"
        ]

    if not (narration or "").strip():
        if len(amount_hits) == 1:
            return None, warnings + [
                "Date and amount matched but narration missing; cannot match UTR remark"
            ]
        return None, warnings + [
            "Multiple payment rows matched date and amount; narration missing to "
            "disambiguate by remark"
        ]

    scored: list[tuple[PaymentRow, bool, float, str]] = []
    remark_failures: list[str] = []
    for row in amount_hits:
        eligible, hard_match, score, explanation = _score_remark_vs_narration(
            row.remark, narration
        )
        if eligible:
            scored.append((row, hard_match, score, explanation))
        else:
            label = row.utr_no or row.beneficiary_name or "row"
            remark_failures.append(f"{label}: {explanation}")

    # Prefer exact/token (hard) matches over fuzzy-only hits.
    hard_hits = [s for s in scored if s[1]]
    if len(hard_hits) == 1:
        return hard_hits[0][0], warnings
    if len(hard_hits) > 1:
        return None, warnings + [
            "Multiple payment rows matched date, amount, and remark/narration"
        ]

    if not scored:
        if len(amount_hits) == 1:
            detail = remark_failures[0] if remark_failures else "remark mismatch"
            return None, warnings + [
                f"Date and amount matched but remark did not match narration ({detail})"
            ]
        return None, warnings + [
            "Payment rows matched date and amount but remark/narration did not uniquely match"
        ] + remark_failures[:5]

    # Fuzzy-only: accept a clear winner by score margin.
    scored.sort(key=lambda item: item[2], reverse=True)
    if len(scored) == 1 or scored[0][2] - scored[1][2] >= 5.0:
        return scored[0][0], warnings

    return None, warnings + [
        "Multiple payment rows matched date, amount, and remark/narration"
    ]


def payment_row_to_fields(row: PaymentRow) -> dict[str, FieldValue]:
    return {
        "documentNumber": FieldValue(
            value=row.utr_no, confidence=row.confidence, page_number=row.page_number, source="utr_no"
        ),
        "documentDate": FieldValue(
            value=row.payment_date,
            confidence=row.confidence,
            page_number=row.page_number,
            source="payment_date",
        ),
        "partyName": FieldValue(
            value=row.beneficiary_name,
            confidence=row.confidence,
            page_number=row.page_number,
            source="beneficiary_name",
        ),
        "amount": FieldValue(
            value=str(row.amount) if row.amount is not None else None,
            confidence=row.confidence,
            page_number=row.page_number,
            source="amount",
        ),
        "description": FieldValue(
            value=row.remark, confidence=row.confidence, page_number=row.page_number, source="remark"
        ),
        "status": FieldValue(
            value=row.status, confidence=row.confidence, page_number=row.page_number, source="status"
        ),
        "utrNo": FieldValue(
            value=row.utr_no, confidence=row.confidence, page_number=row.page_number, source="utr_no"
        ),
    }