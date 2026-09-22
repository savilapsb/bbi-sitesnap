"""Validated screenshot capture primitives for SiteSnap's new capture engine."""

from .models import CaptureOutcome, CaptureResult, PageSnapshot, ValidationResult
from .validation import ValidationPolicy, validate_page

__all__ = [
    "CaptureOutcome",
    "CaptureResult",
    "PageSnapshot",
    "ValidationPolicy",
    "ValidationResult",
    "validate_page",
]
