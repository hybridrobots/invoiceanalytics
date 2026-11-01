"""Layer 3: Greek-first heuristic invoice field extraction."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

from invoice_intelligence.database.models import (
    DocumentDirection,
    ExtractionResult,
    Invoice,
    InvoiceType,
    LineItem,
    VatBreakdownRow,
    parse_date,
    to_decimal,
)

# Unicode letter/digit token (Greek invoice numbers like ΤΙΜ-2024-0015)
_ID_TOKEN = r"([\w\-/.]+)"


def normalize_greek(text: str) -> str:
    """Strip combining marks so τόνος variants match uppercase forms."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


# --- Patterns (Greek-first, English fallback) — matched against accent-stripped text ---

INVOICE_NUMBER_PATTERNS = [
    re.compile(
        rf"(?:Αριθμος\s*(?:Τιμολογιου|Παραστατικου)|Αρ\.?\s*Τιμολογιου|ΑΡΙΘΜΟΣ)\s*[:#]?\s*{_ID_TOKEN}",
        re.IGNORECASE,
    ),
    re.compile(rf"(?:Invoice\s*(?:No|Number)|Inv\.?\s*No\.?)\s*[:#]?\s*{_ID_TOKEN}", re.IGNORECASE),
    re.compile(rf"(?:Τιμολογιο)\s*(?:Αρ\.?|No\.?)?\s*[:#]?\s*{_ID_TOKEN}", re.IGNORECASE),
]

CREDIT_NOTE_MARKERS = [
    re.compile(r"Πιστωτικο\s*Τιμολογιο", re.IGNORECASE),
    re.compile(r"Credit\s*Note", re.IGNORECASE),
    re.compile(r"ΠΙΣΤΩΤΙΚΟ", re.IGNORECASE),
]

RECEIPT_MARKERS = [
    re.compile(r"Αποδειξη", re.IGNORECASE),
    re.compile(r"\bReceipt\b", re.IGNORECASE),
]

# Numeric and day-Mon-year forms (e.g. 02/10/2026, 01-Sep-2022)
_DATE_TOKEN = (
    r"("
    r"\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
    r"|\d{4}[./\-]\d{1,2}[./\-]\d{1,2}"
    r"|\d{1,2}[./\-]\s*[A-Za-z]{3,9}[./\-]\s*\d{2,4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}"
    r")"
)

# Must stay a single group so | alternatives are not broken when embedded in larger patterns.
# Covers: Ημερομηνία, Ημερ/νία - Date, Issue Date, Date of Issue, Έκδοση
_ISSUE_DATE_LABEL = (
    r"(?:"
    r"(?:Ημερομηνια|Ημερ/?νια)"
    r"(?:\s*(?:Εκδοσης|Εκδοσης\s*Τιμολογιου))?"
    r"(?:\s*[-–|/]\s*Date(?:\s*of\s*Issue)?)?"
    r"|Εκδοση"
    r"|Issue\s*Date"
    r"|Date(?:\s*of\s*Issue)?"
    r")"
)

ISSUE_DATE_PATTERNS = [
    # Same line — classic Greek / English labels
    re.compile(
        rf"(?:Ημερομηνια(?:\s*(?:Εκδοσης|Εκδοσης\s*Τιμολογιου))?|Εκδοση|"
        rf"Date(?:\s*of\s*Issue)?|Issue\s*Date)\s*[:\-]?\s*{_DATE_TOKEN}",
        re.IGNORECASE,
    ),
    # Same line — abbreviated Ημερ/νία (- Date) 01-Sep-2022
    re.compile(
        rf"(?:Ημερ/?νια)(?:\s*(?:Εκδοσης|Εκδοσης\s*Τιμολογιου))?"
        rf"(?:\s*[-–|/]\s*Date(?:\s*of\s*Issue)?)?\s*[:\-]?\s*{_DATE_TOKEN}",
        re.IGNORECASE,
    ),
    # Label on its own line, date on the next line (classic + abbreviated)
    re.compile(
        rf"(?m)^[ \t]*{_ISSUE_DATE_LABEL}\s*[:\-]?\s*$\s*{_DATE_TOKEN}",
        re.IGNORECASE,
    ),
]

DUE_DATE_PATTERNS = [
    re.compile(
        rf"(?:Ημερομηνια\s*Ληξης|Ληξη|Οφειλομενη|Due\s*Date|Payment\s*Due)\s*[:\-]?\s*{_DATE_TOKEN}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?m)^[ \t]*(?:Ημερομηνια\s*Ληξης|Ληξη|Due\s*Date)\s*[:\-]?\s*$\s*{_DATE_TOKEN}",
        re.IGNORECASE,
    ),
]

VAT_ID_PATTERNS = [
    re.compile(
        r"(?:Α\.?\s*Φ\.?\s*Μ\.?|ΑΦΜ|VAT(?:\s*ID|\s*No\.?)?|Tax\s*ID)\s*[:#]?\s*([A-Z]{0,2}\s*\d{9}|\d{9})",
        re.IGNORECASE,
    ),
]

# Optional currency marker between label and value, e.g. "Πληρωτέο (€): 370,76"
_CUR_MARK = r"(?:[ \t]*(?:\([€$£]?\)|\([A-Z]{3}\)|\([Ee][Uu][Rr]\)|€|EUR))?"
_AMT = r"([€$£]?\s*[\d.,]+)"

AMOUNT_PATTERNS = {
    "net_amount": [
        re.compile(
            rf"(?:Καθαρη\s*Αξια|Αξια\s*προ\s*ΦΠΑ|Net(?:\s*Amount)?|Subtotal|Υποσυνολο)"
            rf"{_CUR_MARK}[ \t]*[:\-]?[ \t]*{_AMT}",
            re.IGNORECASE,
        ),
    ],
    "vat_amount": [
        re.compile(
            rf"(?:ΦΠΑ(?:\s*\d+[.,]?\d*\s*%?)?|Φ\.Π\.Α\.|VAT(?:\s*Amount)?)"
            rf"{_CUR_MARK}[ \t]*[:\-]?[ \t]*{_AMT}",
            re.IGNORECASE,
        ),
    ],
    "gross_amount": [
        re.compile(
            rf"(?:Συνολικη\s*Αξια|Συνολ\.?\s*Αξια|Συνολο(?:\s*με\s*ΦΠΑ)?|Τελικο\s*Ποσο|"
            rf"Gross(?:\s*Amount)?|Total(?:\s*Amount)?(?!\s*Payable)|"
            rf"Πληρωτεο(?:\s*Ποσο)?)"
            rf"{_CUR_MARK}[ \t]*[:\-]?[ \t]*{_AMT}",
            re.IGNORECASE,
        ),
    ],
    "total_payable": [
        re.compile(
            rf"(?:Πληρωτεο(?:\s*Ποσο)?|Amount\s*Payable|Total\s*Payable|Οφειλομενο)"
            rf"{_CUR_MARK}[ \t]*[:\-]?[ \t]*{_AMT}",
            re.IGNORECASE,
        ),
    ],
    "discount_amount": [
        re.compile(rf"(?:Εκπτωση|Discount){_CUR_MARK}[ \t]*[:\-]?[ \t]*{_AMT}", re.IGNORECASE),
    ],
    "withholding_tax": [
        re.compile(
            rf"(?:Παρακρατηση(?:\s*φορου)?|Withholding(?:\s*Tax)?)"
            rf"{_CUR_MARK}[ \t]*[:\-]?[ \t]*{_AMT}",
            re.IGNORECASE,
        ),
    ],
}

# Label-only lines used in columnar Greek e-invoice PDFs (amount often on the previous line)
AMOUNT_LABEL_LINES = {
    "gross_amount": re.compile(
        r"^(?:Συνολικη\s*Αξια|Συνολ\.?\s*Αξια|Συνολο(?:\s*με\s*ΦΠΑ)?|Τελικο\s*Ποσο|"
        r"Gross(?:\s*Amount)?|Total(?:\s*Amount)?(?!\s*Payable)|"
        r"Πληρωτεο(?:\s*Ποσο)?)" + _CUR_MARK + r"[ \t]*[:\-]?[ \t]*$",
        re.IGNORECASE,
    ),
    "total_payable": re.compile(
        r"^(?:Πληρωτεο(?:\s*Ποσο)?|Amount\s*Payable|Total\s*Payable|Οφειλομενο)"
        + _CUR_MARK
        + r"[ \t]*[:\-]?[ \t]*$",
        re.IGNORECASE,
    ),
    "net_amount": re.compile(
        r"^(?:Καθαρη\s*Αξια|Αξια\s*προ\s*ΦΠΑ|Net(?:\s*Amount)?|Subtotal|Υποσυνολο)"
        + _CUR_MARK
        + r"[ \t]*[:\-]?[ \t]*$",
        re.IGNORECASE,
    ),
    "vat_amount": re.compile(
        r"^(?:ΦΠΑ(?:\s*\d+[.,]?\d*\s*%?)?|Φ\.Π\.Α\.|VAT(?:\s*Amount)?)"
        + _CUR_MARK
        + r"[ \t]*[:\-]?[ \t]*$",
        re.IGNORECASE,
    ),
}

_PLAIN_AMOUNT_LINE = re.compile(r"^[€$£]?\s*([\d.,]+)\s*[€$£]?\s*$")

VAT_RATE_PATTERN = re.compile(r"(?:ΦΠΑ|VAT)\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*%", re.IGNORECASE)

CURRENCY_PATTERNS = [
    (re.compile(r"\bEUR\b|€|ΕΥΡΩ", re.IGNORECASE), "EUR"),
    (re.compile(r"\bUSD\b|\$", re.IGNORECASE), "USD"),
    (re.compile(r"\bGBP\b|£", re.IGNORECASE), "GBP"),
]

# Labels must start a line — otherwise "Προς" matches inside "ΜΟΝΟΠΡΟΣΩΠΗ".
# Do NOT match bare "Επωνυμία" alone — that appears for both parties.
PARTY_LABEL_SUPPLIER = re.compile(
    r"(?m)^[ \t]*(?:Εκδοτης|Πωλητης|Προμηθευτης|Επωνυμια\s*Εκδοτη|"
    r"Supplier|Vendor|Seller|From|Bill\s*From)[ \t]*[:\-]?[ \t]*(.*)$",
    re.IGNORECASE,
)
PARTY_LABEL_CUSTOMER = re.compile(
    r"(?m)^[ \t]*(?:Πελατης|Αγοραστης|Παραληπτης|Επωνυμια\s*Πελατη|"
    r"Customer|Client|Buyer|Bill\s*To|Sold\s*To|Ship\s*To|"
    r"Προς(?=[ \t]*[:\-]|[ \t]*$))[ \t]*[:\-]?[ \t]*(.*)$",
    re.IGNORECASE,
)
SECTION_CUSTOMER = re.compile(r"(?im)^[ \t]*Στοιχεια\s*Πελατη[ \t]*$")
SECTION_SUPPLIER = re.compile(
    r"(?im)^[ \t]*(?:Στοιχεια\s*Εκδοτη|Στοιχεια\s*Πωλητη)[ \t]*$"
)
EPONYMIA_LINE = re.compile(r"(?im)^[ \t]*Επωνυμια[ \t]*[:\-]?[ \t]*(.*)$")
AFM_LINE = re.compile(
    r"(?im)^[ \t]*(?:Α\.?\s*Φ\.?\s*Μ\.?|ΑΦΜ|VAT(?:\s*ID|\s*No\.?)?)[ \t]*[:\-]?[ \t]*([A-Z]{0,2}\s*\d{9}|\d{9})?\s*$"
)
COMPANY_SUFFIX = re.compile(
    r"(?:Α\.?Ε\.?|ΑΕ|Ε\.?Ε\.?|ΕΕ|Ι\.?Κ\.?Ε\.?|ΙΚΕ|Ε\.?Π\.?Ε\.?|ΕΠΕ|Ο\.?Ε\.?|ΟΕ|ΑΕΒΕ|"
    r"ΑΝΩΝΥΜΗ|ΜΟΝΟΠΡΟΣΩΠΗ|"
    r"Ltd|Limited|SA|S\.A\.|GmbH|LLC|Inc)\b",
    re.IGNORECASE,
)
STOP_LINE = re.compile(
    r"^(?:Α\.?\s*Φ\.?\s*Μ\.?|ΑΦΜ|VAT|ΔΟΥ|Δ\.Ο\.Υ|Τηλ|Tel|Email|Fax|IBAN|Ημερομηνια|"
    r"Αριθμος|Καθαρη|ΦΠΑ|Συνολο|Invoice|Date|Address|Οδος|Πολη|Διευθυνση|"
    r"Τ\.?Κ\.?|TK\b|ΑΦΜ/?ΔΟΥ)",
    re.IGNORECASE,
)


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            value = m.group(1).strip()
            # Stop at line break / excessive trailing junk
            value = re.split(r"[\n\r]", value)[0].strip(" .:;-")
            if value:
                return value
    return None


def _line_date(line: str) -> date | None:
    """Parse a whole line or the first date token on it."""
    text = line.strip()
    if not text:
        return None
    parsed = parse_date(text)
    if parsed:
        return parsed
    dm = re.search(_DATE_TOKEN, text)
    if dm:
        return parse_date(dm.group(1))
    return None


def _is_short_header_line(line: str) -> bool:
    """Likely a columnar header cell (Σειρά / Α.Α. / Ημερομηνία / …)."""
    text = line.strip()
    if not text or len(text) > 28:
        return False
    if _line_date(text):
        return False
    if re.search(r"\d{4,}", text):
        return False
    if text.count(" ") > 3:
        return False
    if re.search(r"Τιμολογιο|Invoice|Πιστωτικο|Αποδειξη|Στοιχεια", text, re.IGNORECASE):
        return False
    return True


def _extract_columnar_date(lines: list[str], label_idx: int, label_regex: str) -> date | None:
    """
    Greek e-invoices often emit header labels then values as separate lines:
      Σειρά / Α.Α. / Ημερομηνία / ΜΑΡΚ / Τρόπος Πληρωμής
      Α / 34 / 10/01/2025 / 4000… / …
    Align the label index to the value block underneath.
    """
    if label_idx < 0 or label_idx >= len(lines):
        return None
    # Expand contiguous short header lines around the label
    start = label_idx
    while start > 0 and _is_short_header_line(lines[start - 1]):
        start -= 1
    end = label_idx + 1
    while end < len(lines) and _is_short_header_line(lines[end]):
        end += 1
    headers = lines[start:end]
    try:
        local_idx = next(
            i
            for i, h in enumerate(headers)
            if re.match(rf"^[ \t]*{label_regex}[ \t]*[:\-]?[ \t]*$", h.strip(), re.IGNORECASE)
        )
    except StopIteration:
        return None
    # Value block starts at first non-empty line after headers
    v_start = end
    while v_start < len(lines) and not lines[v_start].strip():
        v_start += 1
    values: list[str] = []
    for j in range(v_start, min(len(lines), v_start + max(len(headers) + 2, 8))):
        cell = lines[j].strip()
        if not cell:
            if values:
                break
            continue
        # Stop when we hit a clear new section title
        if values and (
            re.match(r"^(?:Στοιχεια|Πελατη|Εκδοτης|Επωνυμια|Α\.?\s*Φ\.?\s*Μ)", cell, re.I)
            or (len(cell) > 48 and not _line_date(cell))
        ):
            break
        values.append(cell)
        if len(values) >= len(headers):
            break
    if local_idx < len(values):
        parsed = _line_date(values[local_idx])
        if parsed:
            return parsed
    # Fallback: first date-looking cell in the value block
    for cell in values:
        parsed = _line_date(cell)
        if parsed:
            return parsed
    return None


def _extract_labeled_date(text: str, label_regex: str) -> date | None:
    """Find a date on the same line as a label, nearby lines, or columnar value align."""
    lines = text.splitlines()
    label_line = re.compile(
        rf"^[ \t]*{label_regex}[ \t]*[:\-]?[ \t]*(?:{_DATE_TOKEN})?[ \t]*$",
        re.IGNORECASE,
    )
    for idx, raw in enumerate(lines):
        m = label_line.match(raw)
        if not m:
            continue
        if m.lastindex and m.group(1):
            parsed = parse_date(m.group(1))
            if parsed:
                return parsed
        # Same-line label with trailing date that didn't capture cleanly
        same = _line_date(re.sub(rf"^[ \t]*{label_regex}[ \t]*[:\-]?[ \t]*", "", raw, flags=re.I))
        if same:
            return same
        # Columnar header/value alignment (common on Greek softone / e-invoice PDFs)
        # Try this before "next line" because the next line is often another header (ΜΑΡΚ).
        columnar = _extract_columnar_date(lines, idx, label_regex)
        if columnar:
            return columnar
        # Immediate following lines (skip blanks)
        for nxt in lines[idx + 1 : idx + 6]:
            if not nxt.strip():
                continue
            parsed = _line_date(nxt)
            if parsed:
                return parsed
            break
        # Broader nearby scan: first date within the next ~12 lines
        for nxt in lines[idx + 1 : idx + 13]:
            parsed = _line_date(nxt.strip())
            if parsed:
                return parsed
    return None


def _all_vat_ids(text: str) -> list[str]:
    found: list[str] = []
    for m in VAT_ID_PATTERNS[0].finditer(text):
        raw = re.sub(r"\s+", "", m.group(1).upper())
        if raw and raw not in found:
            found.append(raw)
    return found


def _detect_currency(text: str, default: str = "EUR") -> tuple[str, float]:
    for pat, code in CURRENCY_PATTERNS:
        if pat.search(text):
            return code, 0.9
    return default, 0.4


def _detect_type(text: str) -> tuple[str, float]:
    for pat in CREDIT_NOTE_MARKERS:
        if pat.search(text):
            return InvoiceType.CREDIT_NOTE.value, 0.95
    for pat in RECEIPT_MARKERS:
        if pat.search(text):
            return InvoiceType.RECEIPT.value, 0.8
    if re.search(r"Τιμολογιο|Invoice", text, re.IGNORECASE):
        return InvoiceType.INVOICE.value, 0.85
    return InvoiceType.OTHER.value, 0.4


def _guess_direction(
    text: str,
    *,
    own_vat: str | None = None,
    supplier_vat: str | None = None,
    customer_vat: str | None = None,
) -> tuple[str, float]:
    """Heuristic: if own VAT appears as supplier → sales; as customer → purchase.

    Default is sales (this app's primary use case).
    """
    if own_vat:
        own = re.sub(r"\s+", "", own_vat.upper())
        if supplier_vat and own in supplier_vat.upper():
            return DocumentDirection.SALES.value, 0.9
        if customer_vat and own in customer_vat.upper():
            return DocumentDirection.PURCHASE.value, 0.9
    if re.search(r"Προμηθευτης|Purchase\s*Invoice|Αγορα\s*απο", text, re.IGNORECASE):
        return DocumentDirection.PURCHASE.value, 0.55
    # Default: sales
    return DocumentDirection.SALES.value, 0.6


def _extract_amount(field: str, text: str) -> tuple[Decimal | None, float]:
    patterns = AMOUNT_PATTERNS.get(field, [])
    for pat in patterns:
        matches = list(pat.finditer(text))
        if not matches:
            continue
        # Prefer the last match for totals (often at bottom)
        m = matches[-1]
        value = to_decimal(m.group(1))
        if value is not None:
            return value, 0.75

    # Columnar PDFs: "370,76" on the line above "Πληρωτέο (€):" / "Συνολ. Αξία"
    nearby = _amount_adjacent_to_label(field, text)
    if nearby is not None:
        return nearby, 0.8
    return None, 0.0


def _amount_adjacent_to_label(field: str, text: str) -> Decimal | None:
    """Find an amount on the same/next/previous line as a totals label."""
    label_pat = AMOUNT_LABEL_LINES.get(field)
    if not label_pat:
        return None
    lines = [ln.strip() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        if not label_pat.match(line):
            continue
        # Same line may still contain trailing amount after a soft match miss
        same = _PLAIN_AMOUNT_LINE.match(line)
        if same:
            value = to_decimal(same.group(1))
            if value is not None:
                return value
        # Next non-empty line
        for j in range(i + 1, min(i + 4, len(lines))):
            if not lines[j]:
                continue
            m = _PLAIN_AMOUNT_LINE.match(lines[j])
            if m:
                value = to_decimal(m.group(1))
                if value is not None:
                    return value
            break
        # Previous non-empty line (common in Greek e-invoice layouts)
        for j in range(i - 1, max(-1, i - 4), -1):
            if not lines[j]:
                continue
            m = _PLAIN_AMOUNT_LINE.match(lines[j])
            if m:
                value = to_decimal(m.group(1))
                if value is not None:
                    return value
            break
    return None


def _extract_synola_row(text: str) -> dict[str, Decimal]:
    """Parse the Σύνολα block: discount, net, vat, gross sequence after the header."""
    lines = [ln.strip() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        if not re.match(r"^Συνολα$", line, re.IGNORECASE):
            continue
        nums: list[Decimal] = []
        for j in range(i + 1, min(i + 12, len(lines))):
            m = _PLAIN_AMOUNT_LINE.match(lines[j])
            if not m:
                if nums:
                    break
                continue
            value = to_decimal(m.group(1))
            if value is not None:
                nums.append(value)
            if len(nums) >= 4:
                break
        # Typical order after Σύνολα: discount, net, vat, gross
        if len(nums) >= 4:
            return {
                "discount_amount": nums[0],
                "net_amount": nums[1],
                "vat_amount": nums[2],
                "gross_amount": nums[3],
            }
        if len(nums) == 3:
            return {
                "net_amount": nums[0],
                "vat_amount": nums[1],
                "gross_amount": nums[2],
            }
    return {}


def _extract_vat_breakdown(text: str) -> list[VatBreakdownRow]:
    rows: list[VatBreakdownRow] = []
    for m in VAT_RATE_PATTERN.finditer(text):
        rate = to_decimal(m.group(1))
        if rate is None:
            continue
        # Look ahead for amount near the match
        snippet = text[m.end() : m.end() + 40]
        amt_match = re.search(r"([€]?\s*[\d.,]+)", snippet)
        vat_amt = to_decimal(amt_match.group(1)) if amt_match else None
        rows.append(VatBreakdownRow(vat_rate=rate, vat_amount=vat_amt))
    # Deduplicate by rate
    seen: set[str] = set()
    unique: list[VatBreakdownRow] = []
    for row in rows:
        key = str(row.vat_rate)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _clean_party_name(name: str | None) -> str | None:
    if not name:
        return None
    # Cut trailing tax/address markers, but keep multi-word names (including double spaces)
    name = re.split(r"(?:\t+|\s{2,}(?=ΑΦΜ|Α\.Φ\.Μ|VAT|ΔΟΥ|Δ\.Ο\.Υ)|ΑΦΜ|Α\.Φ\.Μ|VAT|ΔΟΥ|Δ\.Ο\.Υ)", name, maxsplit=1)[0]
    name = name.strip(" .:;-|,")
    name = re.sub(r"\s+", " ", name)
    # Legal Greek company names can be long (multi-line Επωνυμία)
    if len(name) < 2 or len(name) > 300:
        return None
    # Reject pure numbers / VAT-like tokens
    if re.fullmatch(r"[\d\s]+", name):
        return None
    return name


def _is_name_continuation(line: str) -> bool:
    """True if line looks like a wrapped company-name fragment (not an amount/header)."""
    if not line or STOP_LINE.search(line):
        return False
    if re.search(r"[\d.,]{3,}\s*€|^\s*€|^\d+[.,]\d{2}\s*$", line):
        return False
    if PARTY_LABEL_SUPPLIER.match(line) or PARTY_LABEL_CUSTOMER.match(line):
        return False
    # Prefer alphabetic / company wording
    letters = sum(1 for c in line if c.isalpha())
    return letters >= 3


def _name_after_label(pattern: re.Pattern[str], text: str) -> str | None:
    """Read party name on the label line, plus following wrapped legal-name lines."""
    m = pattern.search(text)
    if not m:
        return None

    collected: list[str] = []
    same = _clean_party_name(m.group(1))
    if same:
        collected.append(same)

    # Always collect continuation lines (COCO MAT-style multi-line Επωνυμία)
    tail = text[m.end() :]
    seen_content = False
    for raw in tail.splitlines()[:8]:
        line = raw.strip()
        if not line:
            # Skip the leftover newline after the label match; stop on a real blank gap
            if seen_content:
                break
            continue
        if not _is_name_continuation(line):
            break
        seen_content = True
        cleaned = _clean_party_name(line)
        if cleaned:
            collected.append(cleaned)
        if len(collected) >= 6:
            break

    if not collected:
        return None
    return _clean_party_name(" ".join(collected))


def _names_near_vat_blocks(text: str) -> list[str]:
    """Heuristic: multi-line company names immediately above ΑΦΜ labels."""
    lines = [ln.strip() for ln in text.splitlines()]
    names: list[str] = []
    for i, line in enumerate(lines):
        if not re.search(r"(?:Α\.?\s*Φ\.?\s*Μ\.?|ΑΦΜ|VAT(?:\s*ID|\s*No\.?)?)\s*[:#]?", line, re.I):
            continue
        block: list[str] = []
        for j in range(i - 1, max(-1, i - 7), -1):
            prev = lines[j].strip()
            if not prev:
                if block:
                    break
                continue
            if STOP_LINE.search(prev):
                break
            if PARTY_LABEL_SUPPLIER.match(prev) or PARTY_LABEL_CUSTOMER.match(prev):
                labeled = _clean_party_name(re.split(r"[:\-]", prev, maxsplit=1)[-1])
                if labeled:
                    block.append(labeled)
                break
            if not _is_name_continuation(prev):
                break
            cleaned = _clean_party_name(prev)
            if cleaned:
                block.append(cleaned)
        if block:
            block.reverse()
            joined = _clean_party_name(" ".join(block))
            if joined:
                names.append(joined)
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def _normalize_party_key(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", name).strip().casefold()


def _parties_equal(a: str | None, b: str | None) -> bool:
    ka, kb = _normalize_party_key(a), _normalize_party_key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka in kb or kb in ka


def _section_slice(text: str, start_pat: re.Pattern[str], end_pats: list[re.Pattern[str]]) -> str | None:
    m = start_pat.search(text)
    if not m:
        return None
    start = m.end()
    end = len(text)
    for pat in end_pats:
        n = pat.search(text, start)
        if n and n.start() < end:
            end = n.start()
    return text[start:end]


def _vat_from_block(block: str) -> str | None:
    vat_ids = _all_vat_ids(block)
    if vat_ids:
        return vat_ids[0]
    # ΑΦΜ label on one line, number on the next (common in Greek PDFs)
    lines = [ln.strip() for ln in block.splitlines()]
    for i, line in enumerate(lines):
        if AFM_LINE.match(line) and i + 1 < len(lines):
            nxt = re.sub(r"\s+", "", lines[i + 1])
            if re.fullmatch(r"[A-Z]{0,2}\d{9}", nxt, re.I):
                return nxt.upper()
    return None


def _name_and_vat_from_block(block: str) -> tuple[str | None, str | None]:
    """Extract Επωνυμία + ΑΦΜ from a party section block."""
    name = None
    # Prefer explicit Επωνυμία label inside the section
    if EPONYMIA_LINE.search(block):
        name = _name_after_label(EPONYMIA_LINE, block)
    vat = _vat_from_block(block)
    if not name:
        # Fallback: first company-like lines in the block
        collected: list[str] = []
        for raw in block.splitlines()[:10]:
            line = raw.strip()
            if not line or AFM_LINE.match(line) or STOP_LINE.search(line):
                if collected:
                    break
                continue
            if EPONYMIA_LINE.match(line):
                continue
            if re.fullmatch(r"\d{9}", re.sub(r"\s+", "", line) or ""):
                if collected:
                    break
                continue
            if _is_name_continuation(line):
                cleaned = _clean_party_name(line)
                if cleaned:
                    collected.append(cleaned)
            elif collected:
                break
        if collected:
            name = _clean_party_name(" ".join(collected))
    return name, vat


def _issuer_block_before_customer(text: str) -> str:
    """Top-of-document issuer block before Στοιχεία Πελάτη / line items."""
    end = len(text)
    for pat in (SECTION_CUSTOMER, re.compile(r"(?im)^[ \t]*Κωδ\.", re.M), re.compile(r"(?im)^[ \t]*Περιγραφη", re.M)):
        m = pat.search(text)
        if m and m.start() < end:
            end = m.start()
    return text[:end]


def _extract_parties(
    text: str,
) -> tuple[str | None, str | None, str | None, str | None, float, float]:
    """Return supplier_name, customer_name, supplier_vat, customer_vat, confidences.

    Customer and supplier are never assigned the same identity.
    """
    supplier = _name_after_label(PARTY_LABEL_SUPPLIER, text)
    customer = _name_after_label(PARTY_LABEL_CUSTOMER, text)
    supplier_vat = None
    customer_vat = None
    s_conf = 0.85 if supplier else 0.0
    c_conf = 0.85 if customer else 0.0

    # Section-based extraction (Greek e-invoices)
    cust_section = _section_slice(
        text,
        SECTION_CUSTOMER,
        [
            re.compile(r"(?im)^[ \t]*Κωδ\."),
            re.compile(r"(?im)^[ \t]*Περιγραφη"),
            re.compile(r"(?im)^[ \t]*Συνολα"),
            SECTION_SUPPLIER,
        ],
    )
    if cust_section:
        c_name, c_vat = _name_and_vat_from_block(cust_section)
        if c_name:
            customer = c_name
            c_conf = 0.9
        if c_vat:
            customer_vat = c_vat

    supp_section = _section_slice(
        text,
        SECTION_SUPPLIER,
        [SECTION_CUSTOMER, re.compile(r"(?im)^[ \t]*Κωδ\.")],
    )
    if supp_section:
        s_name, s_vat = _name_and_vat_from_block(supp_section)
        if s_name:
            supplier = s_name
            s_conf = 0.9
        if s_vat:
            supplier_vat = s_vat

    # Issuer often appears as the first Επωνυμία block at the top of the PDF
    if not supplier:
        issuer_block = _issuer_block_before_customer(text)
        s_name, s_vat = _name_and_vat_from_block(issuer_block)
        if s_name and not _parties_equal(s_name, customer):
            supplier = s_name
            s_conf = 0.8
            if s_vat and not supplier_vat:
                supplier_vat = s_vat

    near_vat = _names_near_vat_blocks(text)
    if not supplier:
        for candidate in near_vat:
            if not _parties_equal(candidate, customer):
                supplier = candidate
                s_conf = 0.55
                break
    if not customer:
        for candidate in near_vat:
            if not _parties_equal(candidate, supplier):
                customer = candidate
                c_conf = 0.55
                break

    # Final guard: never keep identical customer/supplier
    if _parties_equal(supplier, customer):
        # Prefer keeping the section-derived customer; clear supplier for manual fill
        if cust_section:
            supplier = None
            s_conf = 0.0
        else:
            customer = None
            c_conf = 0.0

    return supplier, customer, supplier_vat, customer_vat, s_conf, c_conf


def parse_invoice_text(
    text: str,
    *,
    filename: str | None = None,
    pdf_path: str | None = None,
    document_hash: str | None = None,
    default_currency: str = "EUR",
    own_vat: str | None = None,
) -> ExtractionResult:
    """Parse extracted text into a structured Invoice."""
    confidence: dict[str, float] = {}
    warnings: list[str] = []
    original_text = text or ""

    if not text or len(text.strip()) < 10:
        return ExtractionResult(
            invoice=Invoice(
                document_filename=filename,
                original_pdf_path=pdf_path,
                document_hash=document_hash,
                currency=default_currency,
            ),
            confidence={},
            warnings=["No meaningful text extracted from document"],
            raw_text=original_text,
            error="empty_text",
        )

    # Accent-stripped copy for Greek matching; keep original for previews
    text = normalize_greek(text)

    invoice_number = _first_match(INVOICE_NUMBER_PATTERNS, text)
    confidence["invoice_number"] = 0.85 if invoice_number else 0.0
    if not invoice_number:
        warnings.append("Invoice number not found")

    invoice_type, type_conf = _detect_type(text)
    confidence["invoice_type"] = type_conf

    issue_raw = _first_match(ISSUE_DATE_PATTERNS, text)
    issue_date = parse_date(issue_raw) if issue_raw else None
    if not issue_date:
        # Bare / abbreviated "Ημερομηνία" / "Ημερ/νία" with date under or beside it
        issue_date = _extract_labeled_date(text, _ISSUE_DATE_LABEL)
    confidence["issue_date"] = 0.85 if issue_date else 0.0
    if not issue_date:
        # Try any date-like token near top of document (numeric or 01-Sep-2022)
        top = "\n".join(text.splitlines()[:25])
        loose = re.search(
            r"(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
            r"|\d{1,2}[./\-]\s*[A-Za-z]{3,9}[./\-]\s*\d{2,4}"
            r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})",
            top,
        )
        if loose:
            issue_date = parse_date(loose.group(1))
            confidence["issue_date"] = 0.5 if issue_date else 0.0
        if not issue_date:
            warnings.append("Issue date not found")

    due_raw = _first_match(DUE_DATE_PATTERNS, text)
    due_date = parse_date(due_raw) if due_raw else None
    if not due_date:
        due_date = _extract_labeled_date(
            text,
            r"(?:Ημερομηνια\s*Ληξης|Ληξη|Due\s*Date|Payment\s*Due)",
        )
    confidence["due_date"] = 0.8 if due_date else 0.0

    supplier_name, customer_name, section_svat, section_cvat, s_conf, c_conf = _extract_parties(text)
    confidence["supplier_name"] = s_conf
    confidence["customer_name"] = c_conf

    vat_ids = _all_vat_ids(text)
    supplier_vat = section_svat or (vat_ids[0] if vat_ids else None)
    customer_vat = section_cvat
    if not customer_vat and len(vat_ids) > 1:
        # Prefer a VAT that is not the supplier's
        for vid in vat_ids:
            if vid != supplier_vat:
                customer_vat = vid
                break
    if supplier_vat and customer_vat and supplier_vat == customer_vat:
        # Distinct parties must not share the same VAT id assignment
        if section_cvat and section_cvat != supplier_vat:
            customer_vat = section_cvat
        elif len(vat_ids) > 1:
            customer_vat = next((v for v in vat_ids if v != supplier_vat), None)
        else:
            customer_vat = None

    confidence["supplier_vat"] = 0.7 if supplier_vat else 0.0
    confidence["customer_vat"] = 0.65 if customer_vat else 0.0
    if not customer_name:
        warnings.append("Customer name not found — required before sales approval")
    if not supplier_name:
        warnings.append("Supplier name not found")
    if _parties_equal(supplier_name, customer_name):
        warnings.append("Customer and supplier resolved to the same name — correct before approving")

    currency, cur_conf = _detect_currency(text, default_currency)
    confidence["currency"] = cur_conf

    amounts: dict[str, Decimal | None] = {}
    for field in AMOUNT_PATTERNS:
        value, conf = _extract_amount(field, text)
        amounts[field] = value
        confidence[field] = conf

    # Fill gaps from Σύνολα row (common on Greek e-invoices)
    synola = _extract_synola_row(text)
    for key, value in synola.items():
        if amounts.get(key) is None:
            amounts[key] = value
            confidence[key] = max(confidence.get(key, 0.0), 0.7)

    # Πληρωτέο and gross are often the same printed total on Greek invoices
    if amounts.get("total_payable") is None and amounts.get("gross_amount") is not None:
        amounts["total_payable"] = amounts["gross_amount"]
        confidence["total_payable"] = min(confidence.get("gross_amount", 0.5), 0.6)
    if amounts.get("gross_amount") is None and amounts.get("total_payable") is not None:
        amounts["gross_amount"] = amounts["total_payable"]
        confidence["gross_amount"] = min(confidence.get("total_payable", 0.5), 0.75)

    # Prefer Net + VAT when both are known (gross/payable extraction is often wrong)
    net_amt = amounts.get("net_amount")
    vat_amt = amounts.get("vat_amount")
    if net_amt is not None and vat_amt is not None:
        expected_gross = net_amt + vat_amt
        current_gross = amounts.get("gross_amount")
        if current_gross is None or abs(current_gross - expected_gross) > Decimal("0.02"):
            if current_gross is not None:
                warnings.append(
                    f"Gross {current_gross} replaced with Net+VAT {expected_gross}"
                )
            amounts["gross_amount"] = expected_gross
            confidence["gross_amount"] = max(confidence.get("gross_amount", 0.0), 0.85)
        if amounts.get("total_payable") is None:
            amounts["total_payable"] = amounts["gross_amount"]
            confidence["total_payable"] = min(confidence.get("gross_amount", 0.85), 0.8)
        elif abs((amounts["total_payable"] or Decimal("0")) - expected_gross) > Decimal("0.02"):
            # Keep payable if it already matches adjustments; only fill gaps above
            pass

    for field in ("net_amount", "gross_amount"):
        if amounts.get(field) is None:
            warnings.append(f"{field} not found")

    direction, dir_conf = _guess_direction(
        text,
        own_vat=own_vat,
        supplier_vat=supplier_vat,
        customer_vat=customer_vat,
    )
    confidence["document_direction"] = dir_conf

    vat_breakdown = _extract_vat_breakdown(text)

    # Simple line items: lines with a trailing amount (best-effort, low confidence)
    line_items: list[LineItem] = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 5 or len(line) > 160:
            continue
        m = re.search(r"^(.+?)\s+([€]?\s*[\d.,]{2,})$", line)
        if not m:
            continue
        desc = m.group(1).strip()
        amt = to_decimal(m.group(2))
        if amt is None or amt == 0:
            continue
        if re.search(r"ΦΠΑ|Συνολο|Καθαρη|Total|VAT|Subtotal|Πληρωτεο", desc, re.IGNORECASE):
            continue
        if any(c.isalpha() for c in desc):
            line_items.append(LineItem(description=desc, net_amount=amt))
            if len(line_items) >= 15:
                break

    conf_values = [v for v in confidence.values() if v > 0]
    overall = Decimal(str(round(sum(conf_values) / len(conf_values), 4))) if conf_values else Decimal("0")

    invoice = Invoice(
        invoice_number=invoice_number,
        invoice_type=invoice_type,
        document_direction=direction,
        issue_date=issue_date,
        due_date=due_date,
        currency=currency,
        document_filename=filename,
        supplier_name=supplier_name,
        supplier_vat=supplier_vat,
        customer_name=customer_name,
        customer_vat=customer_vat,
        net_amount=amounts.get("net_amount"),
        vat_amount=amounts.get("vat_amount"),
        gross_amount=amounts.get("gross_amount"),
        discount_amount=amounts.get("discount_amount"),
        withholding_tax=amounts.get("withholding_tax"),
        total_payable=amounts.get("total_payable"),
        description=None,
        extraction_confidence=overall,
        original_pdf_path=pdf_path,
        document_hash=document_hash,
        extracted_text_preview=(original_text[:2000] if original_text else None),
        line_items=line_items,
        vat_breakdown=vat_breakdown,
    )

    # Credit notes: if amounts appear positive, keep as-is; analytics apply sign by type
    if invoice_type == InvoiceType.CREDIT_NOTE.value:
        warnings.append("Document identified as credit note — amounts kept as printed; KPIs apply credit treatment")

    return ExtractionResult(
        invoice=invoice,
        confidence=confidence,
        warnings=warnings,
        raw_text=original_text,
    )


def merge_llm_fields(
    result: ExtractionResult,
    llm_data: dict[str, Any],
    *,
    low_confidence_threshold: float = 0.55,
) -> ExtractionResult:
    """Merge LLM output only into missing or low-confidence fields. Never invent over high-confidence values."""
    if not llm_data:
        return result

    invoice = result.invoice
    confidence = dict(result.confidence)
    warnings = list(result.warnings)
    field_map = {
        "invoice_number": "invoice_number",
        "invoice_type": "invoice_type",
        "issue_date": "issue_date",
        "due_date": "due_date",
        "currency": "currency",
        "supplier_name": "supplier_name",
        "supplier_vat": "supplier_vat",
        "customer_name": "customer_name",
        "customer_vat": "customer_vat",
        "net_amount": "net_amount",
        "vat_amount": "vat_amount",
        "gross_amount": "gross_amount",
        "discount_amount": "discount_amount",
        "other_charges": "other_charges",
        "withholding_tax": "withholding_tax",
        "total_payable": "total_payable",
        "description": "description",
        "document_direction": "document_direction",
    }

    money_fields = {
        "net_amount",
        "vat_amount",
        "gross_amount",
        "discount_amount",
        "other_charges",
        "withholding_tax",
        "total_payable",
    }
    date_fields = {"issue_date", "due_date"}

    for llm_key, attr in field_map.items():
        if llm_key not in llm_data:
            continue
        llm_val = llm_data[llm_key]
        if llm_val is None or llm_val == "" or llm_val == "null":
            continue
        current = getattr(invoice, attr)
        current_conf = confidence.get(attr, 0.0)
        # Only fill if missing or low confidence
        if current is not None and current_conf >= low_confidence_threshold:
            continue
        if attr in money_fields:
            parsed = to_decimal(llm_val)
            if parsed is None:
                warnings.append(f"LLM returned non-numeric value for {attr}; ignored")
                continue
            setattr(invoice, attr, parsed)
        elif attr in date_fields:
            parsed_d = parse_date(llm_val)
            if parsed_d is None:
                warnings.append(f"LLM returned invalid date for {attr}; ignored")
                continue
            setattr(invoice, attr, parsed_d)
        else:
            setattr(invoice, attr, str(llm_val).strip())
        confidence[attr] = max(current_conf, 0.6)
        warnings.append(f"Field '{attr}' filled/updated from LLM (low-confidence merge)")

    conf_values = [v for v in confidence.values() if v > 0]
    if conf_values:
        invoice.extraction_confidence = Decimal(str(round(sum(conf_values) / len(conf_values), 4)))
    result.confidence = confidence
    result.warnings = warnings
    result.used_llm = True
    return result
