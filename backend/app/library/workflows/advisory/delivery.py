"""The 투자심의 보고서 of a kept judgement, for the system's report endpoint (not agent-visible).

The agents hand around a reference only (record_advisory_opinion's `artifacts`); the PDF is drawn here, when someone
opens it, from the stored judgement and its evidence — under the store's access rule for the user asking, so a
reference that reaches someone who may not see the meetings it rests on opens nothing.
"""

from __future__ import annotations

from typing import Any

from ..meeting_store import StoreUnavailable, Viewer
from .store import AdvisoryStore


def report_pdf(store: AdvisoryStore, viewer: Viewer, code: str | None) -> dict[str, Any]:
    """{content, filename} or {error, status} (404 unknown or not visible, 503 no store / fonts)."""
    from . import report  # fpdf is imported lazily there

    if not code or not code.strip():
        return {"error": "보고서 id가 필요합니다.", "status": 400}
    try:
        review = store.get(viewer, code)
        if review is None:
            return {"error": f"보고서 '{code}'를 찾을 수 없거나 열람 권한이 없습니다.", "status": 404}
        prior = store.get(viewer, review["prior_code"]) if review.get("prior_code") else None
    except StoreUnavailable as e:
        return {"error": str(e), "status": 503}
    if not report.fonts_available():
        return {"error": "PDF 한글 글꼴이 없어 보고서를 만들 수 없습니다 (REPORT_FONT_DIR).", "status": 503}
    return {"content": report.render(review, prior), "filename": report.filename(review)}
