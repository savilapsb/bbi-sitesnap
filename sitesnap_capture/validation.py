"""Deterministic validation for pages rendered by capture strategies."""

from dataclasses import dataclass
import re
from urllib.parse import urlparse

from .models import CaptureOutcome, PageSnapshot, ValidationResult


CHALLENGE_PHRASES = (
    "are you a human",
    "are you human",
    "checking your browser",
    "complete the security check",
    "enable javascript and cookies to continue",
    "press and hold to confirm",
    "security challenge",
    "verify you are human",
)

BLOCKED_PHRASES = (
    "access denied",
    "request blocked",
    "the request could not be satisfied",
    "you don't have permission to access",
    "you do not have permission to access",
    "your request has been blocked",
)

ERROR_PHRASES = (
    "page not found",
    "the page you requested could not be found",
    "this page is unavailable",
)


@dataclass(frozen=True)
class ValidationPolicy:
    """Brand-specific expectations used by the generic page validator."""

    expected_hosts: tuple[str, ...] = ()
    expected_text: tuple[str, ...] = ()
    minimum_body_characters: int = 200
    minimum_document_height: int = 200
    allow_http_error_statuses: tuple[int, ...] = ()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _host_matches(actual_host: str, expected_host: str) -> bool:
    actual = actual_host.casefold().strip(".")
    expected = expected_host.casefold().strip(".")
    return actual == expected or actual.endswith("." + expected)


def _matched_phrases(text: str, phrases: tuple[str, ...]) -> list[str]:
    return [phrase for phrase in phrases if phrase in text]


def validate_page(
    snapshot: PageSnapshot,
    policy: ValidationPolicy | None = None,
) -> ValidationResult:
    """Classify a rendered page using transport, content, and brand signals.

    Challenge and block evidence takes precedence over positive brand signals so
    that a branded access-denied page cannot be archived as a successful capture.
    """

    policy = policy or ValidationPolicy()
    positive: list[str] = []
    negative: list[str] = []
    reasons: list[str] = []

    if snapshot.navigation_error:
        return ValidationResult(
            outcome=CaptureOutcome.NAVIGATION_ERROR,
            reasons=(snapshot.navigation_error,),
            negative_signals=("navigation error",),
        )

    combined_text = _normalize_text(
        " ".join((snapshot.title, snapshot.body_text, snapshot.html))
    )
    challenge_matches = _matched_phrases(combined_text, CHALLENGE_PHRASES)
    blocked_matches = _matched_phrases(combined_text, BLOCKED_PHRASES)
    error_matches = _matched_phrases(combined_text, ERROR_PHRASES)

    if snapshot.challenge_selectors:
        challenge_matches.append(
            "challenge selectors: " + ", ".join(snapshot.challenge_selectors)
        )

    if challenge_matches:
        negative.extend(challenge_matches)
        reasons.append("The rendered page appears to require human verification.")
        return ValidationResult(
            outcome=CaptureOutcome.CHALLENGE,
            reasons=tuple(reasons),
            positive_signals=tuple(positive),
            negative_signals=tuple(negative),
        )

    if snapshot.status_code in (401, 403):
        blocked_matches.append(f"HTTP {snapshot.status_code}")
    if blocked_matches:
        negative.extend(blocked_matches)
        reasons.append("The rendered page appears to be blocked.")
        return ValidationResult(
            outcome=CaptureOutcome.BLOCKED,
            reasons=tuple(reasons),
            positive_signals=tuple(positive),
            negative_signals=tuple(negative),
        )

    if (
        snapshot.status_code is not None
        and snapshot.status_code >= 400
        and snapshot.status_code not in policy.allow_http_error_statuses
    ):
        negative.append(f"HTTP {snapshot.status_code}")
        if error_matches:
            negative.extend(error_matches)
        reasons.append("The main document returned an HTTP error.")
        return ValidationResult(
            outcome=CaptureOutcome.NAVIGATION_ERROR,
            reasons=tuple(reasons),
            positive_signals=tuple(positive),
            negative_signals=tuple(negative),
        )

    visible_text = _normalize_text(snapshot.body_text)
    if len(visible_text) < policy.minimum_body_characters:
        negative.append(
            f"visible body has {len(visible_text)} characters; "
            f"minimum is {policy.minimum_body_characters}"
        )
    if (
        snapshot.document_height is not None
        and snapshot.document_height < policy.minimum_document_height
    ):
        negative.append(
            f"document height is {snapshot.document_height}px; "
            f"minimum is {policy.minimum_document_height}px"
        )
    if not snapshot.title.strip():
        negative.append("page title is empty")

    if len(negative) >= 2 or not visible_text:
        reasons.append("The rendered page does not contain enough usable content.")
        return ValidationResult(
            outcome=CaptureOutcome.EMPTY,
            reasons=tuple(reasons),
            positive_signals=tuple(positive),
            negative_signals=tuple(negative),
        )

    final_url = snapshot.final_url or snapshot.requested_url
    final_host = urlparse(final_url).hostname or ""
    if policy.expected_hosts:
        if any(_host_matches(final_host, host) for host in policy.expected_hosts):
            positive.append(f"expected host matched: {final_host}")
        else:
            negative.append(f"unexpected final host: {final_host or '<empty>'}")

    if policy.expected_text:
        matches = [text for text in policy.expected_text if text.casefold() in combined_text]
        if matches:
            positive.append("expected text matched: " + ", ".join(matches))
        else:
            negative.append("none of the expected brand markers were found")

    if error_matches:
        negative.extend(error_matches)

    if negative:
        reasons.append("The page rendered, but did not satisfy all validation expectations.")
        return ValidationResult(
            outcome=CaptureOutcome.UNCERTAIN,
            reasons=tuple(reasons),
            positive_signals=tuple(positive),
            negative_signals=tuple(negative),
        )

    positive.append(f"visible body contains {len(visible_text)} characters")
    if snapshot.title.strip():
        positive.append("page title is present")
    reasons.append("The rendered page passed all configured validation checks.")
    return ValidationResult(
        outcome=CaptureOutcome.SUCCESS,
        reasons=tuple(reasons),
        positive_signals=tuple(positive),
        negative_signals=(),
    )
