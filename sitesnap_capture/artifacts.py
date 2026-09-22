"""Persistence helpers for capture diagnostics and failure evidence."""

import json
from pathlib import Path
import re
import shutil

from .models import CaptureResult, PageSnapshot, ValidationResult


def safe_artifact_name(value: str, fallback: str = "capture") -> str:
    """Convert a brand or page label into a portable directory name."""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned[:120] or fallback


def write_failure_artifacts(
    root: Path,
    result: CaptureResult,
    snapshot: PageSnapshot,
    validation: ValidationResult,
    screenshot_source: Path | None = None,
) -> Path:
    """Persist enough evidence to diagnose a rejected capture offline."""

    page_name = safe_artifact_name(snapshot.final_url or snapshot.requested_url)
    output_dir = root / safe_artifact_name(result.brand) / page_name
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = output_dir / "page.html"
    html_path.write_text(snapshot.html, encoding="utf-8")

    screenshot_path = None
    if screenshot_source is not None and screenshot_source.exists():
        screenshot_path = output_dir / ("screenshot" + screenshot_source.suffix.lower())
        shutil.copy2(screenshot_source, screenshot_path)

    result.html_path = html_path
    result.screenshot_path = screenshot_path
    result.validation_reasons = list(validation.reasons)
    if result.failure_reason is None and validation.reasons:
        result.failure_reason = " ".join(validation.reasons)

    diagnostics_path = output_dir / "diagnostics.json"
    result.diagnostics_path = diagnostics_path
    diagnostics = {
        "capture": result.to_dict(),
        "snapshot": {
            "requested_url": snapshot.requested_url,
            "final_url": snapshot.final_url,
            "status_code": snapshot.status_code,
            "title": snapshot.title,
            "body_text_length": len(snapshot.body_text),
            "html_length": len(snapshot.html),
            "document_width": snapshot.document_width,
            "document_height": snapshot.document_height,
            "image_count": snapshot.image_count,
            "loaded_image_count": snapshot.loaded_image_count,
            "challenge_selectors": list(snapshot.challenge_selectors),
            "console_errors": list(snapshot.console_errors),
            "failed_requests": list(snapshot.failed_requests),
            "navigation_error": snapshot.navigation_error,
        },
        "validation": {
            "outcome": validation.outcome.value,
            "accepted": validation.accepted,
            "reasons": list(validation.reasons),
            "positive_signals": list(validation.positive_signals),
            "negative_signals": list(validation.negative_signals),
        },
    }
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_dir
