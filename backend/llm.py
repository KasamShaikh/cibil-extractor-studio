"""Azure OpenAI (Foundry) extraction (Tier 2).

Sends the Document Intelligence content to gpt-4.1-mini and asks for a strict
JSON object matching the parser's structure. Keyless auth via the shared
DefaultAzureCredential. Returns the parsed dict plus real token usage.
"""
from __future__ import annotations

import json
import re
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
- Amounts are integers in rupees: strip currency symbol and commas (1,800,000 -> 1800000). Use 0 for zero, null if absent.
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


def _client(endpoint: str, api_version: str):
    key = (endpoint, api_version)
    if key not in _clients:
        from openai import AzureOpenAI
        from azure.identity import get_bearer_token_provider
        token_provider = get_bearer_token_provider(
            config.credential(), "https://cognitiveservices.azure.com/.default")
        _clients[key] = AzureOpenAI(azure_endpoint=endpoint,
                                    azure_ad_token_provider=token_provider,
                                    api_version=api_version)
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
