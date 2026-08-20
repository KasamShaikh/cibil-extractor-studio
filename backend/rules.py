"""CIBIL business-rules engine (from CIBIL_Extractor_Master_Commands_and_Rules).

Applies the "Generate Report" (Full Detail) standing rules: DPD normalisation
and sequencing, Ownership / Loan Type summaries with totals, purpose-wise
enquiry summary, Column K status derivation and reconciliation checks.
Summaries are recomputed from the parsed accounts (single source of truth);
the PDF's printed figures are used only for reconciliation.
"""
from __future__ import annotations

from datetime import datetime

OWNERSHIP_ORDER = ["Individual", "Joint", "Guarantor"]


# --------------------------------------------------------------------------- #
# DPD helpers
# --------------------------------------------------------------------------- #
def normalize_dpd_token(token: str):
    """STD/000 -> 0 ; XXX/dash/blank -> None (blank) ; digits -> int."""
    if token is None:
        return None
    t = token.strip().upper()
    if t in ("STD", "000", "0"):
        return 0
    if t in ("XXX", "-", "", "NA"):
        return None
    if t.isdigit():
        return int(t)
    return None


def dpd_tokens(raw: str) -> list[str]:
    """Split a DPD cell (may be pipe-separated, latest->oldest) into tokens."""
    if raw is None:
        return []
    parts = [p.strip() for p in str(raw).replace(",", "|").split("|")]
    return [p for p in parts if p != ""]


def dpd_values(raw: str) -> list:
    return [normalize_dpd_token(t) for t in dpd_tokens(raw)]


def peak_dpd(raw: str) -> int:
    vals = [v for v in dpd_values(raw) if isinstance(v, int)]
    return max(vals) if vals else 0


def derive_status(account: dict) -> str:
    """Column K: NPA at 90+ DPD, SMA bands, else keep the printed status."""
    printed = (account.get("status") or "").strip()
    if printed and printed not in ("-", "STD"):
        return printed
    peak = peak_dpd(account.get("dpd"))
    if peak >= 90:
        return "NPA"
    if peak >= 61:
        return "SMA-2"
    if peak >= 31:
        return "SMA-1"
    if peak >= 1:
        return "SMA-0"
    return ""


# --------------------------------------------------------------------------- #
# Sequencing rule
# --------------------------------------------------------------------------- #
def _date_key(account: dict):
    try:
        return datetime.strptime(account["date_opened"], "%d-%m-%Y")
    except (ValueError, KeyError, TypeError):
        return datetime.min


def sequence_accounts(accounts: list[dict]) -> list[dict]:
    """Positive-DPD accounts first, then remaining by Date Opened descending."""
    with_dpd = [a for a in accounts if peak_dpd(a.get("dpd")) > 0]
    without = [a for a in accounts if peak_dpd(a.get("dpd")) == 0]
    with_dpd.sort(key=lambda a: (-peak_dpd(a.get("dpd")), ))
    without.sort(key=_date_key, reverse=True)
    return with_dpd + without


# --------------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------------- #
def _exposure(value):
    """Negative balances do not reduce exposure (rule for Column D)."""
    return value if (value and value > 0) else 0


def ownership_summary(accounts: list[dict]) -> list[dict]:
    rows = []
    grand_s = grand_o = 0
    for cap in OWNERSHIP_ORDER:
        subset = [a for a in accounts if a.get("ownership") == cap]
        s = sum(_exposure(a.get("sanction")) for a in subset)
        o = sum(_exposure(a.get("outstanding")) for a in subset)
        grand_s += s
        grand_o += o
        rows.append({"capacity": cap, "sanction": s, "outstanding": o})
    rows.append({"capacity": "Grand Total", "sanction": grand_s,
                 "outstanding": grand_o, "total": True})
    return rows


def loan_type_summary(accounts: list[dict]) -> list[dict]:
    groups: dict = {}
    for a in accounts:
        key = (a.get("loan_type"), a.get("ownership"))
        g = groups.setdefault(key, {"accounts": 0, "closed": 0, "sanction": 0,
                                    "outstanding": 0})
        g["accounts"] += 1
        if _exposure(a.get("outstanding")) == 0:
            g["closed"] += 1
        g["sanction"] += _exposure(a.get("sanction"))
        g["outstanding"] += _exposure(a.get("outstanding"))

    rows = []
    t_acc = t_closed = t_s = t_o = 0
    for (loan_type, ownership), g in groups.items():
        rows.append({"loan_type": loan_type, "ownership": ownership,
                     "accounts": g["accounts"], "closed": g["closed"],
                     "sanction": g["sanction"], "outstanding": g["outstanding"]})
        t_acc += g["accounts"]
        t_closed += g["closed"]
        t_s += g["sanction"]
        t_o += g["outstanding"]
    rows.append({"loan_type": "Grand Total", "ownership": "", "accounts": t_acc,
                 "closed": t_closed, "sanction": t_s, "outstanding": t_o,
                 "total": True})
    return rows


def purpose_wise_enquiries(detail: list[dict]) -> list[dict]:
    groups: dict = {}
    for e in detail:
        g = groups.setdefault(e.get("purpose") or "Unknown",
                              {"count": 0, "amount": 0})
        g["count"] += 1
        g["amount"] += e.get("amount") or 0
    rows = [{"purpose": k, "count": v["count"], "amount": v["amount"]}
            for k, v in groups.items()]
    rows.sort(key=lambda r: r["count"], reverse=True)
    total_amt = sum(r["amount"] for r in rows)
    total_cnt = sum(r["count"] for r in rows)
    rows.append({"purpose": "Total", "count": total_cnt, "amount": total_amt,
                 "total": True})
    return rows


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #
def _check(label, expected, actual, baseline_ok: bool = True) -> dict:
    if expected is None:
        # Missing baseline: if the report's own totals could not be read at all,
        # flag as unverified (never a silent pass); otherwise this particular figure
        # is just not printed (e.g. a commercial CIR states outstanding, not sanction).
        if baseline_ok:
            return {"check": label, "expected": None, "actual": actual, "ok": True}
        return {"check": label, "expected": None, "actual": actual,
                "ok": False, "unverified": True}
    return {"check": label, "expected": expected, "actual": actual,
            "ok": expected == actual}


def reconcile(parsed: dict, accounts: list[dict]) -> list[dict]:
    cs = parsed["customer"]["credit_summary"]
    # The report's printed totals are the baseline. If even the headline outstanding
    # is unreadable, the summary could not be parsed — checks fail loud (unverified)
    # rather than silently passing on an unfamiliar layout.
    baseline_ok = cs.get("total_outstanding") is not None
    checks = [
        _check("Account count", cs.get("total_accounts"), len(accounts), baseline_ok),
        _check("Total sanction amount", cs.get("total_sanction"),
               sum(_exposure(a.get("sanction")) for a in accounts), baseline_ok),
        _check("Total current outstanding", cs.get("total_outstanding"),
               sum(_exposure(a.get("outstanding")) for a in accounts), baseline_ok),
        _check("Total overdue", cs.get("total_overdue"),
               sum((a.get("overdue") or 0) for a in accounts), baseline_ok),
        _check("Closed accounts (outstanding = 0)", cs.get("closed_accounts"),
               sum(1 for a in accounts if _exposure(a.get("outstanding")) == 0), baseline_ok),
    ]
    enq = parsed["enquiries"]
    checks.append(_check("Enquiries (lifetime)", enq["summary"].get("lifetime"),
                         len(enq["detail"]), baseline_ok))
    return checks


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def apply_rules(parsed: dict) -> dict:
    """Enrich parsed data with sequencing, derived status, summaries,
    reconciliation and a straight-through flag."""
    accounts = [dict(a) for a in parsed["accounts"]]
    for a in accounts:
        a["status_derived"] = derive_status(a)
        a["dpd_display"] = " | ".join(dpd_tokens(a.get("dpd")) or ["000"])
        a["peak_dpd"] = peak_dpd(a.get("dpd"))
    accounts = sequence_accounts(accounts)

    recon = reconcile(parsed, accounts)
    flagged = sum(1 for a in accounts if a.get("flags"))
    cust_flags = _customer_flags(parsed["customer"])
    straight_through = flagged == 0 and not cust_flags and all(c["ok"] for c in recon)

    return {
        **parsed,
        "accounts": accounts,
        "customer_flags": cust_flags,
        "ownership_summary": ownership_summary(accounts),
        "loan_type_summary": loan_type_summary(accounts),
        "purpose_wise": purpose_wise_enquiries(parsed["enquiries"]["detail"]),
        "reconciliation": recon,
        "metrics": {
            "accounts": len(accounts),
            "flagged_accounts": flagged,
            "reconciliation_pass": sum(1 for c in recon if c["ok"]),
            "reconciliation_total": len(recon),
            "straight_through": straight_through,
        },
    }


def _customer_flags(customer: dict) -> list[str]:
    flags: list[str] = []
    d = customer["details"]
    pan = (d.get("pan") or "").strip().upper()
    if not (len(pan) == 10 and pan[:5].isalpha() and pan[5:9].isdigit()
            and pan[9:].isalpha()):
        flags.append("pan")
    score = d.get("cibil_score")
    if not (isinstance(score, int) and 300 <= score <= 900):
        flags.append("cibil_score")
    if not (d.get("customer_name") or "").strip():
        flags.append("customer_name")
    return flags
