from app.models.document_models import DocumentType
from app.services.document_classifier import DocumentClassifier


def test_classify_utr_and_cash_memo_and_grn():
    clf = DocumentClassifier()
    utr = clf.classify_page_text(
        "Pymt_Mode Debit_Acct_no Beneficiary Name Amount Pymt_Date STATUS UTR NO NEFT"
    )
    cash = clf.classify_page_text("CASH MEMO GSTIN Authorised Signature Customer Signature Total Balance")
    grn = clf.classify_page_text("Goods Received Note GRN No Gate Entry No Supplier Name Material Description")
    assert utr.document_type == DocumentType.UTR_STATEMENT
    assert cash.document_type == DocumentType.CASH_MEMO
    assert grn.document_type == DocumentType.GRN


def test_mixed_document_classification():
    clf = DocumentClassifier()
    result = clf.classify_document(
        {
            1: "CASH MEMO GSTIN Authorised Signature Total Balance",
            2: "Goods Received Note GRN No Supplier Name Gate Entry No",
        }
    )
    assert result.primary_type == DocumentType.MIXED