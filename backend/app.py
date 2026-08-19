"""CIBIL Extractor Studio — local FastAPI backend.

Endpoints power a maker-checker workflow:
  upload/extract -> maker review & edit -> submit -> checker approve/reject
  -> download the generated Full Detail Excel workbook.

State is held in-process (single-user PoC). No data leaves the machine.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai_extract, config, excelgen, parser, rules

app = FastAPI(title="CIBIL Extractor Studio", version="1.0.0")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
JOBS: dict[str, dict] = {}
EDITABLE_ACCOUNT_FIELDS = {
    "loan_type", "financier", "pd", "ad12m", "dpd", "ownership",
    "date_opened", "status",
}
AMOUNT_FIELDS = {"sanction", "outstanding", "overdue"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _coerce_amount(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def _enrich(job: dict) -> None:
    """Recompute rules-derived data from the raw parsed payload."""
    data = rules.apply_rules(job["parsed"])
    data["source_file"] = job["parsed"]["source_file"]
    job["data"] = data


def _job_view(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "reject_reason": job.get("reject_reason"),
        "history": job["history"],
        "data": job["data"],
        "processing": job.get("processing"),
    }


def _processing(parsed: dict, data: dict, read_ms: float, rules_ms: float,
                mode: str = "deterministic", ai_tel: dict | None = None) -> dict:
    """Extraction telemetry. Deterministic mode bills no tokens (estimates shown);
    AI mode reports real Document Intelligence / LLM timings and token usage."""
    text_chars = parsed.get("text_chars", 0)
    out_chars = len(json.dumps(data, default=str))
    if mode == "ai" and ai_tel:
        di_ms, llm_ms = ai_tel["di_ms"], ai_tel["llm_ms"]
        deployment = ai_tel["deployment"]
        region = ai_tel.get("region", "")
        return {
            "mode": "ai",
            "engine": f"Document Intelligence + Foundry ({deployment})",
            "engine_detail": "Azure AI Document Intelligence (prebuilt-layout) "
                             f"→ Azure OpenAI {deployment} "
                             f"({region}), keyless via managed identity + RBAC.",
            "deployment": deployment,
            "di_ms": di_ms,
            "llm_ms": llm_ms,
            "rules_ms": round(rules_ms, 1),
            "total_ms": round(di_ms + llm_ms + rules_ms, 1),
            "pages": ai_tel["pages"],
            "text_chars": text_chars,
            "token_kind": "real",
            "input_tokens": ai_tel["prompt_tokens"],
            "output_tokens": ai_tel["completion_tokens"],
            "llm_tokens": ai_tel["total_tokens"],
        }
    return {
        "mode": "deterministic",
        "engine": "Text-layer parsing (pypdfium2)",
        "engine_detail": "Deterministic, in-process — no OCR, no Document "
                         "Intelligence, no LLM call.",
        "read_ms": round(read_ms, 1),
        "rules_ms": round(rules_ms, 1),
        "total_ms": round(read_ms + rules_ms, 1),
        "pages": parsed.get("page_count"),
        "text_chars": text_chars,
        "token_kind": "estimated",
        "input_tokens": (text_chars + 3) // 4,
        "output_tokens": (out_chars + 3) // 4,
        "llm_tokens": 0,
    }


def _get(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
@app.post("/api/extract")
async def extract(file: UploadFile, mode: str = Form("deterministic"),
                  model: str = Form("")):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    content = await file.read()
    mode = (mode or "deterministic").lower()
    ai_tel = None
    t0 = time.perf_counter()
    try:
        if mode == "ai":
            if not config.ai_enabled():
                raise HTTPException(
                    status_code=400,
                    detail="AI mode is not configured. Set DI_ENDPOINT in .env "
                           "and models in models.json.")
            chosen = config.get_model(model) or config.default_model()
            parsed, ai_tel = ai_extract.extract(content, file.filename, chosen)
        else:
            parsed = parser.parse_pdf(content, file.filename)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface extraction errors to the UI
        raise HTTPException(status_code=422,
                            detail=f"Extraction failed ({mode}): {exc}") from exc
    read_ms = (time.perf_counter() - t0) * 1000
    if not parsed["accounts"]:
        raise HTTPException(
            status_code=422,
            detail="No account rows were found — is this a CIBIL report PDF?")

    for i, acc in enumerate(parsed["accounts"]):
        acc["_id"] = i

    job_id = uuid.uuid4().hex[:12]
    engine = (f"AI · {ai_tel['deployment']}" if mode == "ai" and ai_tel
              else "deterministic")
    job = {
        "id": job_id,
        "status": "maker_review",
        "parsed": parsed,
        "reject_reason": None,
        "history": [{"ts": _now(), "action": "Extracted",
                     "note": f"{len(parsed['accounts'])} accounts from "
                             f"{file.filename} ({engine})"}],
    }
    t1 = time.perf_counter()
    _enrich(job)
    rules_ms = (time.perf_counter() - t1) * 1000
    job["processing"] = _processing(parsed, job["data"], read_ms, rules_ms,
                                    mode, ai_tel)
    JOBS[job_id] = job
    return _job_view(job)


# --------------------------------------------------------------------------- #
# Maker edits
# --------------------------------------------------------------------------- #
class CustomerEdit(BaseModel):
    customer_name: str | None = None
    pan: str | None = None
    cibil_score: int | str | None = None
    dob: str | None = None
    aadhaar: str | None = None
    phone: str | None = None
    email: str | None = None
    report_date: str | None = None


class AccountEdit(BaseModel):
    id: int
    loan_type: str | None = None
    financier: str | None = None
    sanction: int | str | None = None
    outstanding: int | str | None = None
    overdue: int | str | None = None
    pd: str | None = None
    ad12m: str | None = None
    dpd: str | None = None
    ownership: str | None = None
    date_opened: str | None = None
    status: str | None = None


class MakerUpdate(BaseModel):
    customer: CustomerEdit | None = None
    accounts: list[AccountEdit] | None = None


@app.put("/api/job/{job_id}")
async def update_job(job_id: str, payload: MakerUpdate):
    job = _get(job_id)
    if job["status"] not in ("maker_review", "rejected"):
        raise HTTPException(status_code=409,
                            detail="Job is locked for editing.")
    parsed = job["parsed"]

    if payload.customer:
        details = parsed["customer"]["details"]
        for key, value in payload.customer.model_dump(exclude_none=True).items():
            if key == "cibil_score":
                m = re.search(r"\d{3}", str(value))
                details[key] = int(m.group()) if m else value
            else:
                details[key] = value

    if payload.accounts:
        by_id = {a["_id"]: a for a in parsed["accounts"]}
        for edit in payload.accounts:
            acc = by_id.get(edit.id)
            if not acc:
                continue
            for key, value in edit.model_dump(exclude_none=True).items():
                if key == "id":
                    continue
                if key in AMOUNT_FIELDS:
                    acc[key] = _coerce_amount(value)
                elif key in EDITABLE_ACCOUNT_FIELDS:
                    acc[key] = value
            acc["flags"] = parser.validate_account(acc)

    job["status"] = "maker_review"
    job["history"].append({"ts": _now(), "action": "Maker edited",
                           "note": "Fields updated"})
    _enrich(job)
    return _job_view(job)


# --------------------------------------------------------------------------- #
# Workflow transitions
# --------------------------------------------------------------------------- #
@app.post("/api/job/{job_id}/submit")
async def submit(job_id: str):
    job = _get(job_id)
    if job["status"] not in ("maker_review", "rejected"):
        raise HTTPException(status_code=409, detail="Nothing to submit.")
    job["status"] = "pending_check"
    job["reject_reason"] = None
    job["history"].append({"ts": _now(), "action": "Submitted to checker",
                           "note": ""})
    return _job_view(job)


class RejectBody(BaseModel):
    reason: str = ""


@app.post("/api/job/{job_id}/approve")
async def approve(job_id: str):
    job = _get(job_id)
    if job["status"] != "pending_check":
        raise HTTPException(status_code=409,
                            detail="Only submitted jobs can be approved.")
    job["status"] = "approved"
    job["excel"] = excelgen.build_workbook(job["data"])
    job["history"].append({"ts": _now(), "action": "Checker approved",
                           "note": "Excel generated"})
    return _job_view(job)


@app.post("/api/job/{job_id}/reject")
async def reject(job_id: str, body: RejectBody):
    job = _get(job_id)
    if job["status"] != "pending_check":
        raise HTTPException(status_code=409,
                            detail="Only submitted jobs can be rejected.")
    job["status"] = "rejected"
    job["reject_reason"] = body.reason
    job["history"].append({"ts": _now(), "action": "Checker rejected",
                           "note": body.reason})
    return _job_view(job)


@app.get("/api/job/{job_id}")
async def get_job(job_id: str):
    return _job_view(_get(job_id))


@app.get("/api/job/{job_id}/excel")
async def download_excel(job_id: str):
    job = _get(job_id)
    if job["status"] != "approved" or "excel" not in job:
        raise HTTPException(status_code=409,
                            detail="Excel is available after checker approval.")
    name = (job["parsed"]["source_file"] or "cibil").rsplit(".", 1)[0]
    headers = {
        "Content-Disposition": f'attachment; filename="{name}_Full_Detail.xlsx"'
    }
    return Response(
        content=job["excel"],
        media_type=("application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"),
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #
@app.get("/")
async def index():
    # Inject an asset version (file mtimes) so browsers always fetch fresh JS/CSS.
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    ver = int(max((FRONTEND / "app.js").stat().st_mtime, (FRONTEND / "styles.css").stat().st_mtime))
    html = html.replace("/app.js", f"/app.js?v={ver}").replace("/styles.css", f"/styles.css?v={ver}")
    return Response(content=html, media_type="text/html")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "jobs": len(JOBS)})


@app.get("/api/capabilities")
async def capabilities():
    return {"ai_enabled": config.ai_enabled(),
            "models": config.models_public(),
            "default_model": (config.default_model() or {}).get("key")}


app.mount("/", StaticFiles(directory=FRONTEND), name="static")
