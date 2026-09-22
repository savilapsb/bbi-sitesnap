"""Persistent, operator-assisted Chrome/Edge capture strategy."""

import asyncio
from enum import Enum
import hashlib
from pathlib import Path
import sys
import time
from typing import Awaitable, Callable

from .artifacts import safe_artifact_name, write_diagnostics, write_failure_artifacts
from .browser import BrowserOptions, _collect_snapshot, playwright_available
from .models import CaptureOutcome, CaptureResult, PageSnapshot, ValidationResult
from .validation import ValidationPolicy, validate_page


class CheckpointAction(str, Enum):
    CONTINUE = "continue"
    SKIP = "skip"
    ABORT = "abort"


class CaptureAborted(RuntimeError):
    """Raised when an operator aborts the interactive capture run."""


Checkpoint = Callable[[str, str], Awaitable[CheckpointAction]]


async def terminal_checkpoint(brand: str, url: str) -> CheckpointAction:
    """Wait for an explicit operator decision while the browser remains open."""

    prompt = (
        f"\nHuman verification detected for {brand} ({url}).\n"
        "Complete the verification in the open browser, then choose "
        "[C]ontinue, [S]kip, or [A]bort: "
    )
    while True:
        answer = (await asyncio.to_thread(input, prompt)).strip().lower()
        actions = {
            "c": CheckpointAction.CONTINUE,
            "continue": CheckpointAction.CONTINUE,
            "s": CheckpointAction.SKIP,
            "skip": CheckpointAction.SKIP,
            "a": CheckpointAction.ABORT,
            "abort": CheckpointAction.ABORT,
        }
        if answer in actions:
            return actions[answer]
        print("Enter Continue, Skip, or Abort.", file=sys.stderr)


def brand_profile_dir(root: Path, brand: str) -> Path:
    """Return the stable, dedicated browser profile directory for a brand."""

    readable = safe_artifact_name(brand) or "brand"
    identity = hashlib.sha256(brand.encode("utf-8")).hexdigest()[:10]
    return Path(root) / f"{readable}_{identity}"


async def _validate_with_checkpoint(
    collect: Callable[[], Awaitable[PageSnapshot]],
    policy: ValidationPolicy,
    brand: str,
    requested_url: str,
    checkpoint: Checkpoint,
) -> tuple[PageSnapshot, ValidationResult]:
    """Recollect after every Continue until validation no longer challenges."""

    while True:
        snapshot = await collect()
        validation = validate_page(snapshot, policy)
        if validation.outcome != CaptureOutcome.CHALLENGE:
            return snapshot, validation
        action = await checkpoint(brand, snapshot.final_url or requested_url)
        if action == CheckpointAction.ABORT:
            raise CaptureAborted(f"Capture aborted by operator for {brand}.")
        if action == CheckpointAction.SKIP:
            return snapshot, ValidationResult(
                outcome=CaptureOutcome.MANUAL_REQUIRED,
                reasons=("Operator skipped the human-verification checkpoint.",),
                positive_signals=validation.positive_signals,
                negative_signals=validation.negative_signals,
            )


async def diagnose_url_persistent(
    url: str,
    brand: str,
    output_dir: Path,
    user_data_root: Path,
    policy: ValidationPolicy | None = None,
    options: BrowserOptions | None = None,
    checkpoint: Checkpoint = terminal_checkpoint,
) -> CaptureResult:
    """Capture with a reusable headed profile and a human challenge checkpoint.

    No challenge is solved programmatically. The same browser context stays open
    while the operator acts, and Continue always triggers a fresh snapshot and
    validation before a screenshot can be accepted.
    """

    if not playwright_available():
        raise RuntimeError(
            "Playwright is required. Install it and an installed Chrome or Edge channel."
        )
    options = options or BrowserOptions(browser="chrome", headless=False)
    if options.headless:
        raise ValueError("The persistent strategy requires a headed browser.")
    if options.browser not in ("chrome", "msedge"):
        raise ValueError("The persistent strategy requires --browser chrome or msedge.")

    from playwright.async_api import async_playwright

    policy = policy or ValidationPolicy()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = brand_profile_dir(Path(user_data_root), brand)
    profile_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    console_errors: list[str] = []
    failed_requests: list[str] = []
    navigation_error: str | None = None
    status: dict[str, int | None] = {"code": None}

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            channel=options.browser,
            headless=False,
            viewport={"width": options.viewport_width, "height": options.viewport_height},
            ignore_https_errors=options.ignore_https_errors,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                f"{request.method} {request.url}: {request.failure or 'unknown failure'}"
            ),
        )
        page.on(
            "response",
            lambda response: status.update(code=response.status)
            if response.request.resource_type == "document"
            and response.request.frame == page.main_frame
            else None,
        )

        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=options.navigation_timeout_ms
            )
            status["code"] = response.status if response is not None else None
        except Exception as exc:
            navigation_error = f"{type(exc).__name__}: {exc}"

        if navigation_error is None:
            try:
                await page.wait_for_load_state("networkidle", timeout=options.settle_timeout_ms)
            except Exception:
                pass
            await asyncio.sleep(0.5)

        async def collect() -> PageSnapshot:
            return await _collect_snapshot(
                page, url, status["code"], console_errors, failed_requests, navigation_error
            )

        try:
            snapshot, validation = await _validate_with_checkpoint(
                collect, policy, brand, url, checkpoint
            )
        except CaptureAborted:
            # Close only after the operator decides, so the window remains available
            # throughout the checkpoint.
            await context.close()
            raise

        capture_name = safe_artifact_name(brand + "_" + url)
        screenshot_path = output_dir / f"{capture_name}.jpeg"
        screenshot_error = None
        if validation.accepted:
            try:
                await page.screenshot(
                    path=screenshot_path,
                    type="jpeg",
                    full_page=True,
                    animations="disabled",
                    timeout=120_000,
                )
            except Exception as exc:
                screenshot_error = f"{type(exc).__name__}: {exc}"

        outcome = CaptureOutcome.SCREENSHOT_ERROR if screenshot_error else validation.outcome
        result = CaptureResult(
            brand=brand,
            requested_url=url,
            strategy="persistent",
            outcome=outcome,
            final_url=snapshot.final_url,
            status_code=snapshot.status_code,
            title=snapshot.title,
            screenshot_path=(
                screenshot_path if validation.accepted and not screenshot_error else None
            ),
            failure_reason=screenshot_error,
            elapsed_seconds=round(time.monotonic() - started, 3),
            validation_reasons=list(validation.reasons),
            console_errors=list(snapshot.console_errors),
            failed_requests=list(snapshot.failed_requests),
        )
        if validation.accepted and screenshot_error is None:
            write_diagnostics(
                output_dir / f"{capture_name}.diagnostics.json", result, snapshot, validation
            )
        else:
            write_failure_artifacts(output_dir / "failures", result, snapshot, validation)

        await context.close()
        return result
