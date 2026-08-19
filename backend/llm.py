"""Azure OpenAI (Foundry) extraction (Tier 2).

Sends the Document Intelligence content to gpt-4.1-mini and asks for a strict
JSON object matching the parser's structure. Keyless auth via the shared
DefaultAzureCredential. Returns the parsed dict plus real token usage.
"""
from __future__ import annotations

import json
import re
import threading
import time

from . import config

ACCOUNT_COLUMNS = [
    "loan_type", "financier", "sanction", "outstanding", "overdue",
    "pd", "ad12m", "dpd", "ownership", "date_opened", "status",
]
ENQUIRY_COLUMNS = ["member", "date", "purpose", "amount"]

SYSTEM_PROMPT = """You are a CIBIL Credit Information Report extraction engine for a bank.
Return ONLY a JSON object with EXACTLY this shape:
{
  "customer": {
    "details": {"customer_name": str, "pan": str, "cibil_score": int, "dob": str,
                 "aadhaar": str, "phone": str, "email": str, "report_date": str},
    "addresses": [str],
    "credit_summary": {"total_accounts": int, "active_accounts": int, "closed_accounts": int,
                        "total_sanction": int, "total_outstanding": int, "total_overdue": int}
  },
  "accounts": [ [loan_type, financier, sanction, outstanding, overdue, pd, ad12m, dpd, ownership, date_opened, status] ],
  "enquiries": {
    "summary": {"last_3m": int, "last_6m": int, "last_12m": int, "lifetime": int},
    "detail": [ [member, date, purpose, amount] ]
  }
}

Each account is an ARRAY in this EXACT column order:
[loan_type, financier, sanction, outstanding, overdue, pd, ad12m, dpd, ownership, date_opened, status]
Each enquiry detail is an ARRAY: [member, date, purpose, amount].

Rules:
- Return EVERY account / tradeline found, in the order they appear. Do not skip any.
- Return each DISTINCT tradeline exactly once. The SAME account's header can repeat across its own detail / history pages — do not emit those repeats as extra accounts. But two DIFFERENT accounts are separate even when their amounts look identical: every "Credit Facility N" block, or every distinct ACCOUNT NO, is its own account — never merge or skip them.
- Amounts are integers in rupees: strip currency symbol and commas (1,800,000 -> 1800000). Use 0 for a printed zero, null if the figure is absent.
- For each account the Sanctioned, Outstanding Balance and Overdue values are the single figures printed at the TOP of its block (labelled SANCTIONED, OUTSTANDING BALANCE and OVERDUE). NEVER use the month-by-month "O/S ₹" or "OD ₹" amounts inside the Asset-Classification / DPD grid — those are historical monthly snapshots, not the current values.
- In credit_summary use the OVERALL COMBINED total across all facilities and lenders (the grand total for the whole report), never a per-institution or per-category subtotal. Fill only the totals the report explicitly prints; if a total is not printed, use null (never 0 or a guess). A commercial report's summary usually prints the total outstanding but no total sanctioned — leave total_sanction null then.
- Dates use DD-MM-YYYY. Counts and cibil_score are integers.
- dpd is the DPD token exactly as shown (e.g. "000"); if a 12-month grid, join latest-to-oldest with " | ".
- ownership is one of: Individual, Joint, Guarantor. financier is the member/lender name only.
- Never invent data. Return JSON only, no prose, no markdown fences."""


def _to_int(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def _row_to_account(row) -> dict:
    acc = row if isinstance(row, dict) else {
        ACCOUNT_COLUMNS[i]: (row[i] if i < len(row) else None)
        for i in range(len(ACCOUNT_COLUMNS))}
    for k in ("sanction", "outstanding", "overdue"):
        acc[k] = _to_int(acc.get(k))
    return acc


def _row_to_enquiry(row) -> dict:
    enq = row if isinstance(row, dict) else {
        ENQUIRY_COLUMNS[i]: (row[i] if i < len(row) else None)
        for i in range(len(ENQUIRY_COLUMNS))}
    enq["amount"] = _to_int(enq.get("amount"))
    return enq


_clients: dict = {}
_token_lock = threading.Lock()
_token = {"value": None, "exp": 0.0}
_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


def _bearer() -> str:
    """Thread-safe cached AAD token so parallel chunk calls don't each shell out to az."""
    if _token["value"] and _token["exp"] - 300 > time.time():
        return _token["value"]
    with _token_lock:
        if _token["value"] and _token["exp"] - 300 > time.time():
            return _token["value"]
        tok = config.credential().get_token(_TOKEN_SCOPE)
        _token["value"], _token["exp"] = tok.token, tok.expires_on
        return _token["value"]


def warm() -> None:
    """Pre-fetch the token once (serially) before parallel extraction begins."""
    try:
        _bearer()
    except Exception:
        pass


def _client(endpoint: str, api_version: str):
    key = (endpoint, api_version)
    if key not in _clients:
        from openai import AzureOpenAI
        # _bearer caches the token behind a lock, so parallel chunk requests reuse
        # one token instead of each shelling out to the Azure CLI (auth stampede).
        # max_retries: the SDK backs off (Retry-After) on 429/5xx so bursts self-heal.
        _clients[key] = AzureOpenAI(azure_endpoint=endpoint,
                                    azure_ad_token_provider=_bearer,
                                    api_version=api_version,
                                    max_retries=6,
                                    timeout=120.0)
    return _clients[key]


def extract(content: str, model: dict) -> dict:
    client = _client(model["endpoint"], model["api_version"])
    kwargs = dict(
        model=model["deployment"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "CIBIL report content:\n\n" + content},
        ],
        response_format={"type": "json_object"},
    )
    if model.get("reasoning"):
        # GPT-5 / o-series: no custom temperature; token budget covers reasoning.
        # "low" reasoning effort keeps structured extraction accurate but faster.
        kwargs["max_completion_tokens"] = 16000
        kwargs["reasoning_effort"] = model.get("reasoning_effort", "low")
    else:
        kwargs["temperature"] = 0
        kwargs["max_tokens"] = 8000

    t0 = time.perf_counter()
    resp = client.chat.completions.create(**kwargs)
    llm_ms = (time.perf_counter() - t0) * 1000

    raw = json.loads(resp.choices[0].message.content)
    data = {
        "customer": raw.get("customer") or {},
        "accounts": [_row_to_account(r) for r in (raw.get("accounts") or [])],
        "enquiries": raw.get("enquiries") or {},
    }
    data["enquiries"]["detail"] = [
        _row_to_enquiry(r) for r in (data["enquiries"].get("detail") or [])]

    usage = resp.usage
    return {
        "data": data,
        "llm_ms": round(llm_ms, 1),
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }
