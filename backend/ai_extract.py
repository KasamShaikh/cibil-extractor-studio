"""AI extraction pipeline: Document Intelligence -> Foundry LLM.

Produces the same parsed structure as `parser.parse_pdf` so the rules engine,
maker-checker workflow and Excel generator are unchanged, plus real telemetry
(DI time, LLM time, token usage).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import di_read, llm, parser


def extract(pdf_bytes: bytes, filename: str, model: dict) -> tuple[dict, dict]:
    # Born-digital PDFs: the embedded text layer is authoritative for values,
    # so it is what we hand to the LLM (DI OCR can corrupt glyphs like the ₹ sign).
    content = parser.extract_text(pdf_bytes)
    # DI output does not feed the LLM for born-digital PDFs, so run the two
    # calls concurrently instead of sequentially — wall time becomes max(DI, LLM).
    with ThreadPoolExecutor(max_workers=2) as pool:
        di_future = pool.submit(di_read.analyze, pdf_bytes)
        llm_future = pool.submit(llm.extract, content, model)
        di = di_future.result()
        out = llm_future.result()
    data = out["data"]

    customer = data.get("customer") or {}
    customer.setdefault("details", {})
    customer.setdefault("addresses", [])
    customer.setdefault("credit_summary", {})

    accounts = data.get("accounts") or []
    for acc in accounts:
        acc["flags"] = parser.validate_account(acc)

    enquiries = data.get("enquiries") or {}
    enquiries.setdefault("summary", {})
    enquiries.setdefault("detail", [])

    parsed = {
        "source_file": filename,
        "page_count": di["pages"],
        "text_chars": len(content),
        "customer": customer,
        "accounts": accounts,
        "enquiries": enquiries,
    }
    telemetry = {
        "di_ms": di["di_ms"],
        "llm_ms": out["llm_ms"],
        "pages": di["pages"],
        "prompt_tokens": out["prompt_tokens"],
        "completion_tokens": out["completion_tokens"],
        "total_tokens": out["total_tokens"],
        "deployment": model["deployment"],
        "region": model.get("region", ""),
        "label": model.get("label", model["deployment"]),
    }
    return parsed, telemetry
