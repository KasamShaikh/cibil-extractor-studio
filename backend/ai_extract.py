"""AI extraction pipeline: Document Intelligence -> Foundry LLM.

Produces the same parsed structure as `parser.parse_pdf` so the rules engine,
maker-checker workflow and Excel generator are unchanged, plus real telemetry
(DI time, LLM time, token usage).
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import di_read, llm, parser

# A single response has an output-token ceiling, so large reports get truncated
# (only the first N accounts come back). The account text is split into chunks
# and extracted in parallel, then merged. A commercial CIR is split on its
# natural per-facility boundary; other reports fall back to character chunks.
CHUNK_CHARS = 6000
# Each "Credit Facility" block's amounts start with this header (the amounts
# precede the "Credit Facility N" label in the text stream), so it marks the
# true start of a facility record — a reliable, repeat-free split point.
FACILITY_MARKER = re.compile(r"SANCTIONED\s+INR", re.I)
BLOCKS_PER_CHUNK = 8
# The commercial "CREDIT SUMMARY (COMBINED AS BORROWER & GUARANTOR)" TOTAL row:
#   TOTAL / Total Lenders : 10 Total CF's : 81 / 81 Non-Delinquent CF 0 Delinquent
#   CF / ₹27,19,27,381(100%) ₹0(0%)  -> grand-total accounts, outstanding, overdue.
_GRAND_TOTAL_RE = re.compile(
    r"TOTAL\s*\n\s*Total Lenders\s*:\s*\d+\s+Total CF['\u2019]?s\s*:\s*(\d+)\s*\n"
    r"\s*\d+\s+Non-Delinquent CF\s+\d+\s+Delinquent CF\s*\n"
    r"\s*\u20b9\s*([\d,]+)\s*\(\d+%\)\s*\u20b9\s*([\d,]+)")


def _detect_report_type(text: str) -> str:
    """Commercial CIR (per-facility 'Credit Facility' blocks) vs an individual /
    consumer CIR (compact account table). Routing decides the chunking strategy."""
    if "COMMERCIAL CREDIT INFORMATION REPORT" in text[:6000].upper():
        return "commercial"
    if len(FACILITY_MARKER.findall(text)) >= 3:
        return "commercial"
    return "individual"


def _chunk_text(text: str) -> list:
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks, cur, size = [], [], 0
    for line in text.split("\n"):  # rows are one line each, so never split a row
        if size + len(line) > CHUNK_CHARS and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def _chunk_commercial(text: str):
    """Split a commercial CIR into a front-matter chunk (customer + credit
    summary + enquiries) plus one chunk per batch of whole facility blocks.
    Splitting on the per-facility amount header keeps each facility's amounts,
    label and DPD grid together and never repeats or splits one — so account
    counts and totals reconstruct exactly. Returns None if the layout is absent."""
    starts = [m.start() for m in FACILITY_MARKER.finditer(text)]
    if len(starts) < 2:
        return None
    front = text[:starts[0]]
    blocks = [text[starts[i]:(starts[i + 1] if i + 1 < len(starts) else len(text))]
              for i in range(len(starts))]
    chunks = [(front, 0)]
    for i in range(0, len(blocks), BLOCKS_PER_CHUNK):
        batch = blocks[i:i + BLOCKS_PER_CHUNK]
        # Tell the model exactly how many facility blocks this chunk holds so it
        # returns one account per block (never merging near-identical facilities).
        hint = (f"[This section contains {len(batch)} credit-facility blocks. "
                f"Return exactly {len(batch)} accounts \u2014 one per 'Credit Facility' "
                f"block, each identified by its own ACCOUNT NO, even if two blocks "
                f"show identical amounts.]\n\n")
        chunks.append((hint + "\n".join(batch), len(batch)))
    return chunks


def _commercial_summary(text: str, n_blocks: int) -> dict:
    """Authoritative grand-total credit summary for a commercial CIR. The number
    of facility blocks is ground truth for the account count, and the COMBINED
    'TOTAL' row gives the grand-total outstanding / overdue. This overrides the
    LLM, whose pick is unstable when the front matter lists several competing
    totals (grand vs per-institution vs per-lender-type)."""
    summary = {"total_accounts": n_blocks}
    m = _GRAND_TOTAL_RE.search(text)
    if m:
        summary["total_outstanding"] = int(m.group(2).replace(",", ""))
        summary["total_overdue"] = int(m.group(3).replace(",", ""))
    return summary


# Consumer / individual CIR: each account is an "ACCOUNT DATES AMOUNTS STATUS"
# block; the page-2 SUMMARY box gives the authoritative totals.
CONSUMER_MARKER = re.compile(r"ACCOUNT DATES AMOUNTS STATUS", re.I)
_CONSUMER_TOTALS_RE = re.compile(
    r"TOTAL:\s*(\d+)\s+HIGH CR/SANC\.?\s*AMT:\s*([\d,]+)\s+CURRENT:\s*([\d,]+)", re.I)
_CONSUMER_ZERO_RE = re.compile(r"ZERO-BALANCE:\s*(\d+)", re.I)
_CONSUMER_OVERDUE_RE = re.compile(r"OVERDUE:\s*([\d,]+)", re.I)
_CONSUMER_ENQ_RE = re.compile(r"All Enquiries\s+(\d+)", re.I)


def _chunk_consumer(text: str):
    """Split a consumer CIR into a front-matter chunk (customer + summary +
    enquiries) plus batches of whole account blocks. Each account starts with an
    'ACCOUNT DATES AMOUNTS STATUS' header, so splitting there keeps every account
    intact and never repeats one. Returns None if the layout is absent."""
    starts = [m.start() for m in CONSUMER_MARKER.finditer(text)]
    if len(starts) < 2:
        return None
    front = text[:starts[0]]
    raw = [text[starts[i]:(starts[i + 1] if i + 1 < len(starts) else len(text))]
           for i in range(len(starts))]
    n = len(raw)
    # Consumer accounts are privacy-masked (MEMBER NAME / ACCOUNT NUMBER = "NOT
    # DISCLOSED"), so two similar tradelines are otherwise indistinguishable and the
    # model may merge them. Tag each block with a unique index to force one per block.
    blocks = [f"[[ ACCOUNT {i} of {n} ]]\n{b}" for i, b in enumerate(raw, 1)]
    chunks = [(front, 0)]
    for i in range(0, len(blocks), BLOCKS_PER_CHUNK):
        batch = blocks[i:i + BLOCKS_PER_CHUNK]
        hint = (f"[This section contains {len(batch)} account blocks, each marked "
                f"'[[ ACCOUNT n of {n} ]]'. Return exactly {len(batch)} accounts \u2014 "
                f"one per marked block, even if two blocks look identical.]\n\n")
        chunks.append((hint + "\n".join(batch), len(batch)))
    return chunks


def _consumer_summary(text: str, n_blocks: int) -> dict:
    """Authoritative credit summary for a consumer CIR from the page-2 SUMMARY box
    (TOTAL accounts / HIGH CR-SANC amount / CURRENT balance / ZERO-BALANCE / OVERDUE).
    Overrides the LLM so the reconciliation baseline is stable."""
    summary = {"total_accounts": n_blocks}
    m = _CONSUMER_TOTALS_RE.search(text)
    if m:
        summary["total_sanction"] = int(m.group(2).replace(",", ""))
        summary["total_outstanding"] = int(m.group(3).replace(",", ""))
    z = _CONSUMER_ZERO_RE.search(text)
    if z:
        summary["closed_accounts"] = int(z.group(1))
    od = _CONSUMER_OVERDUE_RE.search(text)
    if od:
        summary["total_overdue"] = int(od.group(1).replace(",", ""))
    return summary


def _consumer_enquiry_count(text: str):
    m = _CONSUMER_ENQ_RE.search(text)
    return int(m.group(1)) if m else None


def _merge(outs: list, dedup: bool = True) -> dict:
    """All accounts across chunks, plus the richest customer / enquiries block."""
    accounts, seen = [], set()
    best_customer, best_cscore = {}, -1
    best_enq, best_escore = {}, -1
    for o in outs:
        d = o.get("data") or {}
        for a in (d.get("accounts") or []):
            # Char-chunk fallback can repeat an account across arbitrary splits,
            # so dedup by identity there. Commercial boundary chunks never repeat
            # (and two facilities can legitimately share type/amount/date), so
            # dedup is off on that path — see extract().
            if dedup:
                key = (a.get("loan_type"), a.get("financier"),
                       a.get("sanction"), a.get("date_opened"))
                if key in seen:
                    continue
                seen.add(key)
            accounts.append(a)
        c = d.get("customer") or {}
        cs = c.get("credit_summary") or {}
        det = c.get("details") or {}
        cscore = ((1 if cs.get("total_accounts") is not None else 0)
                  + (1 if det.get("pan") else 0)
                  + (1 if det.get("customer_name") else 0))
        if cscore > best_cscore:
            best_cscore, best_customer = cscore, c
        e = d.get("enquiries") or {}
        escore = len(e.get("detail") or [])
        if escore > best_escore:
            best_escore, best_enq = escore, e
    return {"customer": best_customer, "accounts": accounts, "enquiries": best_enq}


def _extract_chunk(chunk: str, model: dict, expected=None, attempts: int = 3):
    """Extract one chunk, retrying if it returns fewer accounts than the block count
    it was given (the model occasionally merges look-alike, privacy-masked accounts)
    or on a transient failure. Keeps the richest result across attempts."""
    best, best_n = None, -1
    for _ in range(attempts):
        try:
            r = llm.extract(chunk, model)
        except Exception:
            continue
        n = len((r.get("data") or {}).get("accounts") or [])
        if n > best_n:
            best, best_n = r, n
        if not expected or best_n >= expected:
            break
    return best


def extract(pdf_bytes: bytes, filename: str, model: dict) -> tuple[dict, dict]:
    t_read = time.perf_counter()
    content = parser.extract_text(pdf_bytes)
    pages = parser.page_count(pdf_bytes)
    read_ms = (time.perf_counter() - t_read) * 1000
    report_type = _detect_report_type(content)
    has_text = len(content.strip()) >= 200
    di_ms, di_ran, ocr_used = 0.0, False, False
    # Born-digital PDFs carry an authoritative text layer, so Document Intelligence
    # (OCR) is skipped — it adds no accuracy (its OCR mangles the ₹ glyph) and is the
    # slow step. DI runs only for a scan (no text layer), where OCR is the only input.
    if not has_text:
        di = di_read.analyze(pdf_bytes)
        content, pages, di_ms = di["content"], di["pages"], di["di_ms"]
        di_ran, ocr_used = True, True
        report_type = _detect_report_type(content)
    # Chunk per the report type: commercial and consumer CIRs each split on their
    # own per-account boundary (repeat-free, so no dedup); an unknown layout falls
    # back to character chunks (which can repeat, so dedup on merge).
    structured = (_chunk_commercial(content) if report_type == "commercial"
                  else _chunk_consumer(content))
    # Chunks are (text, expected_account_count); the char-chunk fallback has no
    # per-chunk count, so no retry-if-short there.
    chunks, dedup = ((structured, False) if structured
                     else ([(c, None) for c in _chunk_text(content)], True))
    # Warm one token serially so the parallel calls reuse it instead of each thread
    # invoking the Azure CLI at once (an auth stampede).
    llm.warm()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, min(len(chunks), 9))) as pool:
        llm_futures = [pool.submit(_extract_chunk, c, model, exp) for c, exp in chunks]
        outs = [r for r in (f.result() for f in llm_futures) if r]
    llm_wall_ms = (time.perf_counter() - t0) * 1000
    if not outs:
        raise RuntimeError("extraction produced no results")
    data = _merge(outs, dedup) if len(outs) > 1 else outs[0]["data"]

    customer = data.get("customer") or {}
    customer.setdefault("details", {})
    customer.setdefault("addresses", [])
    customer.setdefault("credit_summary", {})
    if structured:
        # Read the credit summary from the report structure; the LLM's pick is
        # unstable across the front matter's several competing totals.
        if report_type == "commercial":
            customer["credit_summary"].update(
                _commercial_summary(content, len(FACILITY_MARKER.findall(content))))
        else:
            customer["credit_summary"].update(
                _consumer_summary(content, len(CONSUMER_MARKER.findall(content))))

    accounts = data.get("accounts") or []
    for acc in accounts:
        acc["flags"] = parser.validate_account(acc)

    enquiries = data.get("enquiries") or {}
    enquiries.setdefault("summary", {})
    enquiries.setdefault("detail", [])
    if structured and report_type != "commercial":
        enq_total = _consumer_enquiry_count(content)
        if enq_total is not None:
            enquiries["summary"]["lifetime"] = enq_total

    parsed = {
        "source_file": filename,
        "page_count": pages,
        "text_chars": len(content),
        "customer": customer,
        "accounts": accounts,
        "enquiries": enquiries,
    }
    telemetry = {
        "read_ms": round(read_ms, 1),
        "di_ms": round(di_ms, 1),
        "di_ran": di_ran,
        "ocr_used": ocr_used,
        "llm_ms": round(llm_wall_ms, 1),
        "report_type": report_type,
        "chunks": len(chunks),
        "pages": pages,
        "prompt_tokens": sum(o["prompt_tokens"] for o in outs),
        "completion_tokens": sum(o["completion_tokens"] for o in outs),
        "total_tokens": sum(o["total_tokens"] for o in outs),
        "deployment": model["deployment"],
        "region": model.get("region", ""),
        "label": model.get("label", model["deployment"]),
    }
    return parsed, telemetry
