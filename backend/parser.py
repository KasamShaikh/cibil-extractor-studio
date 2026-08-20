"""CIBIL PDF parser — pypdfium2 ingestion (permissive Apache/BSD licence).

Extracts customer details, account rows, enquiries and the printed summary
figures from a (synthetic) CIBIL Credit Information Report PDF. Deterministic,
fully offline (no OCR, no network). Fields that fail a sanity check are flagged
for Maker review. pypdfium2 emits one line per row, so rows are parsed with
position-anchored regexes.
"""
from __future__ import annotations

import re
from datetime import datetime

import pypdfium2 as pdfium

# Exact Account Summary column order (matches the Excel template A:K).
ACCOUNT_HEADERS = [
    "Loan Type",
    "Financier Name",
    "Sanction Amount",
    "Current Outstanding",
    "Overdue Amount",
    "PD",
    "AD 12 M",
    "DPD Last 12 Months",
    "Ownership",
    "Date Opened",
    "NPA/ SMA/ Suit Filed/ Write off",
]

# Financier column must never contain these label words (validation rule #9).
FORBIDDEN_FINANCIER_WORDS = [
    "Current", "Repayment", "Amount", "Balance", "Tenure", "Account", "Type",
]

# Known CIBIL account types, used to split "<loan type> <financier>".
KNOWN_LOAN_TYPES = [
    "COMMERCIAL VEHICLE LOAN", "HOUSING LOAN|HOUSE LOAN", "HOUSING LOAN",
    "HOUSE LOAN", "OVERDRAFT", "AUTO LOAN", "CREDIT CARD", "LOAN AGAINST",
    "PERSONAL LOAN", "BUSINESS LOAN", "GOLD LOAN", "TWO-WHEELER LOAN",
]

_NOISE = ("SAMPLE", "SYNTHETIC", "Synthetic test data", "Testing Notice",
          "official TransUnion")

# One account per line: <type+financier> <3 amounts> <pd> <ad> <dpd> <ownership> <date> <status>
_ACCOUNT_RE = re.compile(
    r"^(?P<rest>.+?)\s+"
    r"(?P<sanction>[^\s\d]*[\d,]+)\s+"
    r"(?P<outstanding>[^\s\d]*[\d,]+)\s+"
    r"(?P<overdue>[^\s\d]*[\d,]+)\s+"
    r"(?P<pd>\S+)\s+(?P<ad>\S+)\s+(?P<dpd>\S+)\s+"
    r"(?P<own>Individual|Joint|Guarantor)\s+"
    r"(?P<date>\d{2}-\d{2}-\d{4})\s+(?P<status>.*)$"
)

# One enquiry per line: <member> <date> <purpose> <amount>
_ENQUIRY_RE = re.compile(
    r"^(?P<member>.+?)\s+(?P<date>\d{2}-\d{2}-\d{4})\s+"
    r"(?P<purpose>.+?)\s+(?P<amount>[^\s\d]*[\d,]+)$"
)

_CUST_LABELS = {
    "Customer Name": "customer_name", "PAN": "pan", "CIBIL Score": "cibil_score",
    "Aadhaar / UID": "aadhaar", "Phone Number(s)": "phone",
    "Email ID(s)": "email", "Date of Birth": "dob", "Report Date": "report_date",
}
_SUMMARY_LABELS = {
    "Total Accounts": "total_accounts", "Active Accounts": "active_accounts",
    "Closed Accounts": "closed_accounts", "Total Sanction Amount": "total_sanction",
    "Total Current Outstanding": "total_outstanding", "Total Overdue": "total_overdue",
}
_ENQ_PERIODS = {
    "Last 3 Months": "last_3m", "Last 6 Months": "last_6m",
    "Last 12 Months": "last_12m", "Lifetime": "lifetime",
}


def _amount(text):
    if text is None:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _valid_date(text: str) -> bool:
    try:
        datetime.strptime((text or "").strip(), "%d-%m-%Y")
        return True
    except ValueError:
        return False


def _is_noise(line: str) -> bool:
    return any(m in line for m in _NOISE)


def _pages(data: bytes) -> list[str]:
    pdf = pdfium.PdfDocument(data)
    try:
        return [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
    finally:
        pdf.close()


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def extract_text(data: bytes) -> str:
    """Full text-layer content (authoritative for born-digital PDFs; the LLM
    input in AI mode)."""
    return "\n".join(_pages(data))


def page_count(data: bytes) -> int:
    """Page count straight from the PDF (no OCR / Document Intelligence call)."""
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def _split_type_financier(rest: str) -> tuple[str, str]:
    for loan_type in sorted(KNOWN_LOAN_TYPES, key=len, reverse=True):
        if rest.upper().startswith(loan_type):
            return rest[:len(loan_type)], rest[len(loan_type):].strip()
    parts = rest.split(None, 1)
    return (parts[0] if parts else rest), (parts[1] if len(parts) > 1 else "")


def validate_account(acc: dict) -> list[str]:
    """Return the list of low-confidence field keys for an account."""
    flags: list[str] = []
    if acc.get("sanction") is None:
        flags.append("sanction")
    if acc.get("outstanding") is None:
        flags.append("outstanding")
    if not _valid_date(acc.get("date_opened") or ""):
        flags.append("date_opened")
    if acc.get("ownership") not in ("Individual", "Joint", "Guarantor"):
        flags.append("ownership")
    financier = acc.get("financier") or ""
    if any(w.lower() in financier.lower() for w in FORBIDDEN_FINANCIER_WORDS):
        flags.append("financier")
    return flags


def _build_account(match: re.Match) -> dict:
    loan_type, financier = _split_type_financier(match.group("rest").strip())
    acc = {
        "loan_type": loan_type,
        "financier": financier,
        "sanction": _amount(match.group("sanction")),
        "outstanding": _amount(match.group("outstanding")),
        "overdue": _amount(match.group("overdue")),
        "pd": match.group("pd"),
        "ad12m": match.group("ad"),
        "dpd": match.group("dpd"),
        "ownership": match.group("own"),
        "date_opened": match.group("date"),
        "status": (match.group("status").strip() or "-"),
    }
    acc["flags"] = validate_account(acc)
    return acc


def _parse_accounts(pages: list[str]) -> list[dict]:
    accounts: list[dict] = []
    for text in pages:
        for line in _lines(text):
            if _is_noise(line):
                continue
            m = _ACCOUNT_RE.match(line)
            if m and _valid_date(m.group("date")):
                accounts.append(_build_account(m))
    return accounts


# --------------------------------------------------------------------------- #
# Customer details + credit summary (page 1)
# --------------------------------------------------------------------------- #
def _pairs_in_line(line: str, labels) -> dict:
    hits = []
    for label in labels:
        for m in re.finditer(r"(?<![A-Za-z0-9])" + re.escape(label) +
                             r"(?![A-Za-z0-9])", line):
            hits.append((m.start(), m.end(), label))
    hits.sort()
    out = {}
    for i, (_, end, label) in enumerate(hits):
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(line)
        out[label] = line[end:nxt].strip()
    return out


def _parse_customer(page1: str) -> dict:
    cust = {v: None for v in _CUST_LABELS.values()}
    summary = {v: None for v in _SUMMARY_LABELS.values()}
    addresses: list[str] = []
    all_labels = list(_CUST_LABELS) + list(_SUMMARY_LABELS)

    for line in _lines(page1):
        addr = re.match(r"^(\d{1,2})\s+(.+)$", line)
        if addr and ("," in addr.group(2)):
            addresses.append(addr.group(2).strip())
            continue
        pairs = _pairs_in_line(line, all_labels)
        for label, value in pairs.items():
            if label in _CUST_LABELS and cust[_CUST_LABELS[label]] is None:
                cust[_CUST_LABELS[label]] = value
            elif label in _SUMMARY_LABELS and summary[_SUMMARY_LABELS[label]] is None:
                summary[_SUMMARY_LABELS[label]] = value

    for k in ("total_accounts", "active_accounts", "closed_accounts",
              "total_sanction", "total_outstanding", "total_overdue"):
        summary[k] = _amount(summary[k]) if summary[k] else None

    score = cust.get("cibil_score") or ""
    m = re.search(r"\d{3}", score)
    cust["cibil_score"] = int(m.group()) if m else (score or None)

    return {"details": cust, "addresses": addresses, "credit_summary": summary}


# --------------------------------------------------------------------------- #
# Enquiries (page 5)
# --------------------------------------------------------------------------- #
def _parse_enquiries(pages: list[str]) -> dict:
    summary = {v: None for v in _ENQ_PERIODS.values()}
    detail: list[dict] = []
    for text in pages:
        lines = _lines(text)
        if not any("Enquiry" in ln for ln in lines):
            continue
        started = False
        for line in lines:
            for label, key in _ENQ_PERIODS.items():
                m = re.match(r"^" + re.escape(label) + r"\s+(\d+)$", line)
                if m:
                    summary[key] = int(m.group(1))
            if "Enquiry Amount" in line or "Enquiry Purpose" in line:
                started = True
                continue
            if not started or _is_noise(line):
                continue
            m = _ENQUIRY_RE.match(line)
            if m and _valid_date(m.group("date")):
                detail.append({
                    "member": m.group("member").strip(),
                    "date": m.group("date"),
                    "purpose": m.group("purpose").strip(),
                    "amount": _amount(m.group("amount")),
                })
    return {"summary": summary, "detail": detail}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def parse_pdf(data: bytes, filename: str) -> dict:
    pages = _pages(data)
    full_text = "\n".join(pages)
    customer = _parse_customer(pages[0] if pages else "")
    accounts = _parse_accounts(pages)
    enquiries = _parse_enquiries(pages)

    return {
        "source_file": filename,
        "page_count": len(pages),
        "text_chars": len(full_text),
        "customer": customer,
        "accounts": accounts,
        "enquiries": enquiries,
    }
