from decimal import Decimal

from app.validators.amount_validator import compare_amounts, normalize_amount
from app.validators.date_validator import compare_dates, normalize_date
from app.validators.description_validator import compare_descriptions
from app.validators.party_validator import compare_party_names


def test_normalize_amount_strips_currency_and_commas():
    assert normalize_amount("₹ 10,250.00") == Decimal("10250.00")
    assert normalize_amount("85850") == Decimal("85850.00")


def test_amount_match_and_mismatch():
    ok = compare_amounts("10250", "10250.00")
    bad = compare_amounts("10250", "10200")
    assert ok.matched and ok.status == "MATCH"
    assert not bad.matched and bad.status == "MISMATCH"


def test_date_formats_indian():
    assert normalize_date("08/11/26").isoformat() == "2026-11-08"
    assert normalize_date("07-11-2026").isoformat() == "2026-11-07"
    # Finance midnight IST is stored as previous-day 18:30Z
    assert normalize_date("2026-11-07T18:30:00.000Z").isoformat() == "2026-11-08"


def test_date_match_and_mismatch():
    ok = compare_dates("2026-11-07T00:00:00Z", "07-11-2026")
    bad = compare_dates("2026-11-07T00:00:00Z", "08-11-2026")
    assert ok.matched
    assert not bad.matched


def test_party_exact_and_ambiguous():
    exact = compare_party_names("A M COOLING SYSTEMS", "Am Cooling Systems")
    assert exact.matched
    ambiguous = compare_party_names("Swaminarayan Gurukul", "Some Other Vendor Pvt Ltd")
    assert not ambiguous.matched


def test_description_token_overlap():
    result = compare_descriptions("PCB Repair kitchen", "PCB Repair indoor & outdoor for kitchen")
    assert result.matched or result.status == "AMBIGUOUS"