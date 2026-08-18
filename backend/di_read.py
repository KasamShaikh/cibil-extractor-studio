"""Azure AI Document Intelligence reader (Tier 1).

Runs the prebuilt-layout model over the uploaded PDF and returns the extracted
content (Markdown when available) plus page count. Keyless auth via the shared
DefaultAzureCredential.
"""
from __future__ import annotations

import time

from . import config


def analyze(pdf_bytes: bytes) -> dict:
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

    client = DocumentIntelligenceClient(config.DI_ENDPOINT, config.credential())
    request = AnalyzeDocumentRequest(bytes_source=pdf_bytes)

    t0 = time.perf_counter()
    try:
        poller = client.begin_analyze_document(
            "prebuilt-layout", request, output_content_format="markdown",
            polling_interval=2)
    except TypeError:
        # Older SDK without output_content_format
        poller = client.begin_analyze_document(
            "prebuilt-layout", request, polling_interval=2)
    result = poller.result()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    content = result.content or ""
    pages = len(result.pages or [])
    return {"content": content, "pages": pages, "di_ms": round(elapsed_ms, 1)}
