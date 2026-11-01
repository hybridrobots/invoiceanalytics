"""Tests for Greek-first invoice parsing."""

from decimal import Decimal

from invoice_intelligence.database.models import InvoiceType
from invoice_intelligence.ingestion.invoice_parser import parse_invoice_text


def test_parse_greek_invoice(sample_text: str) -> None:
    result = parse_invoice_text(sample_text, filename="sample.pdf")
    inv = result.invoice
    assert inv.invoice_number == "ΤΙΜ-2024-0015"
    assert inv.issue_date is not None
    assert inv.issue_date.isoformat() == "2024-03-15"
    assert inv.due_date is not None
    assert inv.currency == "EUR"
    assert inv.net_amount == Decimal("1000.00") or inv.net_amount == Decimal("1000")
    assert inv.vat_amount == Decimal("240.00") or inv.vat_amount == Decimal("240")
    assert inv.gross_amount == Decimal("1240.00") or inv.gross_amount == Decimal("1240")
    assert inv.supplier_vat == "998877665"
    assert inv.customer_vat == "112233445"
    assert inv.supplier_name and "ΝΕΦΕΛΗ" in inv.supplier_name
    assert inv.customer_name and "ΑΚΡΟΠΟΛΗ" in inv.customer_name
    assert inv.invoice_type == InvoiceType.INVOICE.value
    assert result.error is None


def test_customer_on_next_line() -> None:
    text = """
    ΤΙΜΟΛΟΓΙΟ
    Αριθμός Τιμολογίου: X-9
    Ημερομηνία Έκδοσης: 01/02/2024
    Πελάτης:
    ΟΛΥΜΠΟΣ ΤΕΧΝΙΚΗ ΑΕ
    ΑΦΜ: 123456789
    Εκδότης: ΕΜΕΙΣ ΑΕ
    ΑΦΜ: 987654321
    Καθαρή Αξία: 10,00
    ΦΠΑ 24%: 2,40
    Συνολική Αξία: 12,40
    EUR
    """
    result = parse_invoice_text(text)
    assert result.invoice.customer_name is not None
    assert "ΟΛΥΜΠΟΣ" in result.invoice.customer_name


def test_columnar_hmerominia_value_below_headers() -> None:
    """Softone-style PDFs emit label row then value row as separate lines."""
    text = """
    Τιμολόγιο Παροχής Υπηρεσιών
    Σειρά
    Α.Α.
    Ημερομηνία
    ΜΑΡΚ
    Τρόπος Πληρωμής
    Α
    34
    10/01/2025
    400008249497030
    Στοιχεία Πελάτη
    Αριθμός Τιμολογίου: A34
    Πελάτης: TEST CUSTOMER AE
    ΑΦΜ: 111111111
    Εκδότης: US AE
    ΑΦΜ: 222222222
    Καθαρή Αξία: 10,00
    ΦΠΑ 24%: 2,40
    Συνολική Αξία: 12,40
    EUR
    """
    result = parse_invoice_text(text)
    assert result.invoice.issue_date is not None
    assert result.invoice.issue_date.isoformat() == "2025-01-10"


def test_date_under_hmerominia_label() -> None:
    text = """
    ΤΙΜΟΛΟΓΙΟ
    Αριθμός Τιμολογίου: INV-77
    Ημερομηνία
    02/10/2026
    Πελάτης: TEST CUSTOMER AE
    ΑΦΜ: 111111111
    Εκδότης: US AE
    ΑΦΜ: 222222222
    Καθαρή Αξία: 10,00
    ΦΠΑ 24%: 2,40
    Συνολική Αξία: 12,40
    EUR
    """
    result = parse_invoice_text(text)
    assert result.invoice.issue_date is not None
    assert result.invoice.issue_date.isoformat() == "2026-10-02"
    assert result.invoice.document_direction == "sales"


def test_date_hmer_slash_nia_english_month() -> None:
    text = """
    ΤΙΜΟΛΟΓΙΟ
    Αριθμός Τιμολογίου: INV-SEP-1
    Ημερ/νία - Date 01-Sep-2022
    Πελάτης: TEST CUSTOMER AE
    ΑΦΜ: 111111111
    Εκδότης: US AE
    ΑΦΜ: 222222222
    Καθαρή Αξία: 10,00
    ΦΠΑ 24%: 2,40
    Συνολική Αξία: 12,40
    EUR
    """
    result = parse_invoice_text(text)
    assert result.invoice.issue_date is not None
    assert result.invoice.issue_date.isoformat() == "2022-09-01"


def test_issue_date_english_label_same_line() -> None:
    text = """
    INVOICE
    Invoice Number: INV-EN-1
    Issue Date: 15/03/2024
    Customer: TEST CUSTOMER LTD
    VAT: 111111111
    Supplier: US LTD
    VAT: 222222222
    Net Amount: 10.00
    VAT: 2.40
    Gross Amount: 12.40
    EUR
    """
    result = parse_invoice_text(text)
    assert result.invoice.issue_date is not None
    assert result.invoice.issue_date.isoformat() == "2024-03-15"


def test_issue_date_english_label_next_line() -> None:
    text = """
    INVOICE
    Invoice Number: INV-EN-2
    Issue Date
    15/03/2024
    Customer: TEST CUSTOMER LTD
    VAT: 111111111
    Supplier: US LTD
    VAT: 222222222
    Net Amount: 10.00
    VAT Amount: 2.40
    Total Amount: 12.40
    EUR
    """
    result = parse_invoice_text(text)
    assert result.invoice.issue_date is not None
    assert result.invoice.issue_date.isoformat() == "2024-03-15"


def test_date_of_issue_label() -> None:
    text = """
    INVOICE
    Invoice Number: INV-EN-3
    Date of Issue: 02/10/2026
    Customer: TEST CUSTOMER LTD
    VAT: 111111111
    Supplier: US LTD
    VAT: 222222222
    Net Amount: 10.00
    VAT Amount: 2.40
    Gross Amount: 12.40
    EUR
    """
    result = parse_invoice_text(text)
    assert result.invoice.issue_date is not None
    assert result.invoice.issue_date.isoformat() == "2026-10-02"


def test_plirwteo_euro_paren_amount() -> None:
    text = """
    ΤΙΜΟΛΟΓΙΟ
    Αριθμός Τιμολογίου: P-1
    Ημερομηνία
    02/10/2026
    Πελάτης: TEST AE
    ΑΦΜ: 111111111
    Εκδότης: US AE
    ΑΦΜ: 222222222
    Καθαρή Αξία: 299,00
    ΦΠΑ 24%: 71,76
    Πληρωτέο (€): 370,76
    """
    result = parse_invoice_text(text)
    assert result.invoice.gross_amount == Decimal("370.76")
    assert result.invoice.total_payable == Decimal("370.76")
    assert result.invoice.net_amount == Decimal("299.00") or result.invoice.net_amount == Decimal("299")


def test_amount_on_line_above_plirwteo_and_synola() -> None:
    """Real Greek e-invoice layout: value above label, abbreviated Συνολ. Αξία."""
    text = """
    ΤΙΜΟΛΟΓΙΟ
    Αριθμός Τιμολογίου: A62
    Ημερομηνία
    30/10/2026
    Πελάτης:
    COCO MAT ΜΟΝΟΠΡΟΣΩΠΗ AΝΩΝΥΜΗ
    ΑΦΜ: 997656116
    Σύνολα
    0,00
    299,00
    71,76
    370,76
    0,00
    0,00
    0,00
    0,00
    370,76
    Συνολ. Αξία
    (-) Παρακρατούμενοι
    0,00
    0,00
    0,00
    370,76
    Πληρωτέο (€):
    Παρατηρήσεις:
    """
    result = parse_invoice_text(text)
    assert result.invoice.gross_amount == Decimal("370.76")
    assert result.invoice.total_payable == Decimal("370.76")
    assert result.invoice.net_amount == Decimal("299.00") or result.invoice.net_amount == Decimal("299")
    assert result.invoice.vat_amount == Decimal("71.76")


def test_coco_mat_multiline_customer_not_cropped_by_pros() -> None:
    """Regression: 'Προς' must not match inside ΜΟΝΟΠΡΟΣΩΠΗ."""
    text = """
    ΤΙΜΟΛΟΓΙΟ ΠΩΛΗΣΗΣ
    Αριθμός Τιμολογίου: CM-100
    Ημερομηνία
    02/10/2026
    Πελάτης:
    COCO MAT ΜΟΝΟΠΡΟΣΩΠΗ AΝΩΝΥΜΗ
    ΒΙΟΜΗΧΑΝΙΚΗ ΚΑΙ ΕΜΠΟΡΙΚΗ ΕΤΑΙΡΕΙΑ
    ΟΙΚΙΑΚΟΥ ΚΑΙ ΞΕΝΟΔΟΧΕΙΑΚΟΥ
    ΕΞΟΠΛΙΣΜΟΥ
    ΑΦΜ: 999888777
    Εκδότης: ΝΕΦΕΛΗ ΥΠΗΡΕΣΙΕΣ ΑΕ
    ΑΦΜ: 998877665
    Καθαρή Αξία: 100,00
    ΦΠΑ 24%: 24,00
    Συνολική Αξία: 124,00
    EUR
    """
    result = parse_invoice_text(text)
    name = result.invoice.customer_name or ""
    assert "COCO MAT" in name
    assert "ΜΟΝΟΠΡΟΣΩΠΗ" in name
    assert "ΕΞΟΠΛΙΣΜΟΥ" in name
    assert not name.startswith("ΩΠΗ")
    assert result.invoice.issue_date is not None
    assert result.invoice.issue_date.isoformat() == "2026-10-02"
    assert result.invoice.document_direction == "sales"
    assert result.invoice.supplier_name and "ΝΕΦΕΛΗ" in result.invoice.supplier_name
    assert result.invoice.customer_name != result.invoice.supplier_name


def test_customer_supplier_from_stoixeia_sections() -> None:
    text = """
    Επωνυμία
    ΝΤΟΥΝΕΤΑΣ ΑΝΤΩΝΙΟΣ ΣΤΑΥΡΟΣ
    Α.Φ.Μ.
    101454569
    Τιμολόγιο Παροχής Υπηρεσιών
    Ημερομηνία
    30/10/2026
    Αριθμός Τιμολογίου: A62
    Στοιχεία Πελάτη
    Α.Φ.Μ.:
    997656116
    Επωνυμία:
    COCO MAT ΜΟΝΟΠΡΟΣΩΠΗ AΝΩΝΥΜΗ
    ΒΙΟΜΗΧΑΝΙΚΗ ΚΑΙ ΕΜΠΟΡΙΚΗ ΕΤΑΙΡΕΙΑ
    ΟΙΚΙΑΚΟΥ ΚΑΙ ΞΕΝΟΔΟΧΕΙΑΚΟΥ
    ΕΞΟΠΛΙΣΜΟΥ
    Διεύθυνση:
    ΒΙΠΕ ΞΑΝΘΗΣ
    Σύνολα
    0,00
    299,00
    71,76
    370,76
    370,76
    Πληρωτέο (€):
    """
    result = parse_invoice_text(text)
    assert result.invoice.supplier_name and "ΝΤΟΥΝΕΤΑΣ" in result.invoice.supplier_name
    assert result.invoice.customer_name and "COCO MAT" in result.invoice.customer_name
    assert "ΝΤΟΥΝΕΤΑΣ" not in (result.invoice.customer_name or "")
    assert result.invoice.supplier_vat == "101454569"
    assert result.invoice.customer_vat == "997656116"
    assert result.invoice.gross_amount == Decimal("370.76")



def test_parse_credit_note(credit_text: str) -> None:
    result = parse_invoice_text(credit_text)
    assert result.invoice.invoice_type == InvoiceType.CREDIT_NOTE.value
    assert result.invoice.invoice_number == "ΠΙΣ-2024-0003"
    assert any("credit note" in w.lower() for w in result.warnings)


def test_empty_text_does_not_invent() -> None:
    result = parse_invoice_text("")
    assert result.invoice.invoice_number is None
    assert result.invoice.net_amount is None
    assert result.error == "empty_text"


def test_gross_autofilled_from_net_plus_vat() -> None:
    text = """
    ΤΙΜΟΛΟΓΙΟ Α123
    Ημερομηνία
    15/03/2024
    Καθαρή Αξία: 299,00
    ΦΠΑ: 71,76
    Συνολική Αξία: 1,00
    """
    result = parse_invoice_text(text)
    assert result.invoice.net_amount == Decimal("299.00") or result.invoice.net_amount == Decimal("299")
    assert result.invoice.vat_amount == Decimal("71.76")
    assert result.invoice.gross_amount == Decimal("370.76")
    assert any("replaced with Net+VAT" in w for w in result.warnings)
