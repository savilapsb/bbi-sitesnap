"""Data models shared by capture strategies, validation, and reporting."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class CaptureOutcome(str, Enum):
    """The normalized outcome of a page navigation or capture attempt."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    CHALLENGE = "challenge"
    EMPTY = "empty"
    NAVIGATION_ERROR = "navigation_error"
    SCREENSHOT_ERROR = "screenshot_error"
    MANUAL_REQUIRED = "manual_required"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class PageSnapshot:
    """Browser observations collected before a screenshot is accepted."""

    requested_url: str
    final_url: str | None = None
    status_code: int | None = None
    title: str = ""
    body_text: str = ""
    html: str = ""
    document_width: int | None = None
    document_height: int | None = None
    image_count: int | None = None
    loaded_image_count: int | None = None
    challenge_selectors: tuple[str, ...] = ()
    console_errors: tuple[str, ...] = ()
    failed_requests: tuple[str, ...] = ()
    navigation_error: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    """The validator's decision and the evidence used to reach it."""

    outcome: CaptureOutcome
    reasons: tuple[str, ...] = ()
    positive_signals: tuple[str, ...] = ()
    negative_signals: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.outcome == CaptureOutcome.SUCCESS


@dataclass
class CaptureResult:
    """Serializable record of one complete capture attempt."""

    brand: str
    requested_url: str
    strategy: str
    outcome: CaptureOutcome
    final_url: str | None = None
    status_code: int | None = None
    title: str | None = None
    screenshot_path: Path | None = None
    html_path: Path | None = None
    diagnostics_path: Path | None = None
    failure_reason: str | None = None
    elapsed_seconds: float = 0.0
    validation_reasons: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation suitable for run reports."""

        result = asdict(self)
        result["outcome"] = self.outcome.value
        for key in ("screenshot_path", "html_path", "diagnostics_path"):
            value = result[key]
            result[key] = str(value) if value is not None else None
        return result
