"""Standard Playwright browser strategy for one-page diagnostic captures."""

import asyncio
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import time
from typing import Any

from .artifacts import safe_artifact_name, write_diagnostics, write_failure_artifacts
from .models import CaptureOutcome, CaptureResult, PageSnapshot
from .validation import ValidationPolicy, validate_page


CHALLENGE_SELECTORS = (
    "iframe[src*='captcha' i]",
    "iframe[src*='challenge' i]",
    "[id*='captcha' i]",
    "[class*='captcha' i]",
    "#challenge-stage",
    "#px-captcha",
)


@dataclass(frozen=True)
class BrowserOptions:
    """Runtime settings for the standard diagnostic browser."""

    browser: str = "chromium"
    headless: bool = False
    navigation_timeout_ms: int = 60_000
    settle_timeout_ms: int = 10_000
    viewport_width: int = 1280
    viewport_height: int = 800
    ignore_https_errors: bool = False


def playwright_available() -> bool:
    """Return whether the optional Playwright dependency is installed."""

    if importlib.util.find_spec("playwright") is None:
        return False
    return importlib.util.find_spec("playwright.async_api") is not None


def _playwright_install_message() -> str:
    return (
        "Playwright is required for browser diagnostics. Install it with "
        "'python -m pip install playwright' and then install Chromium with "
        "'python -m playwright install chromium'."
    )


async def _matching_challenge_selectors(page: Any) -> tuple[str, ...]:
    matches = []
    for selector in CHALLENGE_SELECTORS:
        try:
            if await page.locator(selector).count() > 0:
                matches.append(selector)
        except Exception:
            continue
    return tuple(matches)


async def _collect_snapshot(
    page: Any,
    requested_url: str,
    status_code: int | None,
    console_errors: list[str],
    failed_requests: list[str],
    navigation_error: str | None,
) -> PageSnapshot:
    """Collect browser observations without assuming that navigation succeeded."""

    title = ""
    body_text = ""
    html = ""
    dimensions: dict[str, int | None] = {
        "width": None,
        "height": None,
        "images": None,
        "loaded_images": None,
    }

    try:
        title = await page.title()
    except Exception:
        pass
    try:
        body_text = await page.locator("body").inner_text(timeout=5_000)
    except Exception:
        pass
    try:
        html = await page.content()
    except Exception:
        pass
    try:
        dimensions = await page.evaluate(
            """
            () => ({
                width: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
                height: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
                images: document.images.length,
                loaded_images: Array.from(document.images).filter(
                    image => image.complete && image.naturalWidth > 0
                ).length,
            })
            """
        )
    except Exception:
        pass

    return PageSnapshot(
        requested_url=requested_url,
        final_url=page.url or None,
        status_code=status_code,
        title=title,
        body_text=body_text,
        html=html,
        document_width=dimensions.get("width"),
        document_height=dimensions.get("height"),
        image_count=dimensions.get("images"),
        loaded_image_count=dimensions.get("loaded_images"),
        challenge_selectors=await _matching_challenge_selectors(page),
        console_errors=tuple(console_errors),
        failed_requests=tuple(failed_requests),
        navigation_error=navigation_error,
    )


async def diagnose_url(
    url: str,
    brand: str,
    output_dir: Path,
    policy: ValidationPolicy | None = None,
    options: BrowserOptions | None = None,
) -> CaptureResult:
    """Render, validate, screenshot, and report on one URL.

    This strategy deliberately uses an unmodified browser identity: no proxy,
    user-agent override, fingerprint script, or stealth package is applied.
    """

    if not playwright_available():
        raise RuntimeError(_playwright_install_message())

    from playwright.async_api import async_playwright

    policy = policy or ValidationPolicy()
    options = options or BrowserOptions()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    console_errors: list[str] = []
    failed_requests: list[str] = []
    navigation_error = None
    status_code = None

    async with async_playwright() as playwright:
        launch_options: dict[str, Any] = {"headless": options.headless}
        if options.browser in ("chrome", "msedge"):
            launch_options["channel"] = options.browser
        browser = await playwright.chromium.launch(**launch_options)
        context = await browser.new_context(
            viewport={
                "width": options.viewport_width,
                "height": options.viewport_height,
            },
            ignore_https_errors=options.ignore_https_errors,
        )
        page = await context.new_page()
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

        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=options.navigation_timeout_ms,
            )
            status_code = response.status if response is not None else None
        except Exception as exc:
            navigation_error = f"{type(exc).__name__}: {exc}"

        if navigation_error is None:
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=options.settle_timeout_ms,
                )
            except Exception:
                # Many healthy sites continuously poll; network-idle is best effort.
                pass
            await asyncio.sleep(0.5)

        snapshot = await _collect_snapshot(
            page=page,
            requested_url=url,
            status_code=status_code,
            console_errors=console_errors,
            failed_requests=failed_requests,
            navigation_error=navigation_error,
        )
        validation = validate_page(snapshot, policy)
        capture_name = safe_artifact_name(brand + "_" + url)
        screenshot_path = output_dir / f"{capture_name}.jpeg"
        screenshot_error = None
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

        outcome = validation.outcome
        if screenshot_error is not None:
            outcome = CaptureOutcome.SCREENSHOT_ERROR
        result = CaptureResult(
            brand=brand,
            requested_url=url,
            strategy="standard",
            outcome=outcome,
            final_url=snapshot.final_url,
            status_code=snapshot.status_code,
            title=snapshot.title,
            screenshot_path=screenshot_path if screenshot_error is None else None,
            failure_reason=screenshot_error,
            elapsed_seconds=round(time.monotonic() - started, 3),
            validation_reasons=list(validation.reasons),
            console_errors=list(snapshot.console_errors),
            failed_requests=list(snapshot.failed_requests),
        )

        if validation.accepted and screenshot_error is None:
            write_diagnostics(
                output_dir / f"{capture_name}.diagnostics.json",
                result,
                snapshot,
                validation,
            )
        else:
            failure_root = output_dir / "failures"
            write_failure_artifacts(
                failure_root,
                result,
                snapshot,
                validation,
                screenshot_source=screenshot_path if screenshot_path.exists() else None,
            )
            if screenshot_path.exists():
                screenshot_path.unlink()

        await context.close()
        await browser.close()
        return result
