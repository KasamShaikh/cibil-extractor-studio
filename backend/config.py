"""Configuration and a shared Azure credential for the optional AI pipeline.

All Azure access is keyless — DefaultAzureCredential resolves the developer's
`az login` locally, or the workload/managed identity when hosted. Endpoints are
read from a local .env (see .env.example); no keys or secrets are stored.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # dotenv optional
    pass

DI_ENDPOINT = os.getenv("DI_ENDPOINT", "").strip()

# Model registry. In containers / production the model is configured entirely from
# environment variables (FOUNDRY_*). For local dev you can instead drop a
# models.json (git-ignored) in the project root; see models.json.example.
_MODELS_PATH = Path(__file__).resolve().parent.parent / "models.json"


def _env_model() -> list[dict]:
    """Single model from environment — the production / container path."""
    endpoint = os.getenv("FOUNDRY_ENDPOINT", "").strip()
    deployment = os.getenv("FOUNDRY_DEPLOYMENT", "").strip()
    if not (endpoint and deployment):
        return []
    region = os.getenv("FOUNDRY_REGION", "").strip()
    return [{
        "key": deployment,
        "label": f"{deployment} · Foundry" + (f" ({region})" if region else ""),
        "endpoint": endpoint,
        "deployment": deployment,
        "api_version": os.getenv("FOUNDRY_API_VERSION", "2025-04-01-preview"),
        "reasoning": os.getenv("FOUNDRY_REASONING", "true").strip().lower() == "true",
        "region": region,
        "default": True,
    }]


def _load_models() -> list[dict]:
    env_models = _env_model()
    if env_models:
        return env_models
    try:
        if _MODELS_PATH.exists():
            models = (json.loads(_MODELS_PATH.read_text(encoding="utf-8")) or {}).get("models")
            if models:
                return models
    except Exception:
        pass
    return []


MODELS = _load_models()
_credential = None


def models_public() -> list[dict]:
    return [{"key": m["key"], "label": m.get("label", m["key"]),
             "region": m.get("region", ""), "default": bool(m.get("default"))}
            for m in MODELS]


def default_model() -> dict | None:
    for m in MODELS:
        if m.get("default"):
            return m
    return MODELS[0] if MODELS else None


def get_model(key: str) -> dict | None:
    for m in MODELS:
        if m["key"] == key:
            return m
    return default_model()


def ai_enabled() -> bool:
    return bool(DI_ENDPOINT and MODELS)


def credential():
    """Lazily create one shared DefaultAzureCredential for DI + OpenAI."""
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential
        _credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=True)
    return _credential
