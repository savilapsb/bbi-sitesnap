import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import sitesnap_diagnose
from sitesnap_capture.models import CaptureOutcome, CaptureResult, PageSnapshot
from sitesnap_capture.persistent import (
    CaptureAborted,
    CheckpointAction,
    _validate_with_checkpoint,
    _persistent_launch_options,
    brand_profile_dir,
)
from sitesnap_capture.browser import BrowserOptions
from sitesnap_capture.validation import ValidationPolicy


def challenge_snapshot() -> PageSnapshot:
    return PageSnapshot(
        requested_url="https://www.hoka.com/",
        final_url="https://www.hoka.com/en/us/",
        status_code=403,
        title="hoka.com",
        html="<iframe src='captcha'></iframe>",
        challenge_selectors=("iframe[src*='captcha' i]",),
    )


def accepted_snapshot() -> PageSnapshot:
    text = "HOKA running shoes and apparel. " * 20
    return PageSnapshot(
        requested_url="https://www.hoka.com/",
        final_url="https://www.hoka.com/en/us/",
        status_code=200,
        title="HOKA Running Shoes",
        body_text=text,
        html=f"<html><body>{text}</body></html>",
        document_height=1800,
    )


class CheckpointTests(unittest.TestCase):
    def test_windows_launch_keeps_browser_sandbox_enabled(self):
        with patch("sitesnap_capture.persistent.sys.platform", "win32"):
            launch_options = _persistent_launch_options(
                BrowserOptions(browser="chrome", headless=False)
            )

        self.assertEqual(["--no-sandbox"], launch_options["ignore_default_args"])
        self.assertFalse(launch_options["headless"])

    def test_non_windows_launch_keeps_playwright_defaults(self):
        with patch("sitesnap_capture.persistent.sys.platform", "linux"):
            launch_options = _persistent_launch_options(
                BrowserOptions(browser="chrome", headless=False)
            )

        self.assertNotIn("ignore_default_args", launch_options)

    def test_continue_recollects_and_revalidates(self):
        collect = AsyncMock(side_effect=[challenge_snapshot(), accepted_snapshot()])
        checkpoint = AsyncMock(return_value=CheckpointAction.CONTINUE)

        snapshot, validation = asyncio.run(
            _validate_with_checkpoint(
                collect,
                ValidationPolicy(expected_hosts=("hoka.com",), expected_text=("HOKA",)),
                "Hoka",
                "https://www.hoka.com/",
                checkpoint,
            )
        )

        self.assertEqual(2, collect.await_count)
        self.assertEqual(1, checkpoint.await_count)
        self.assertEqual(200, snapshot.status_code)
        self.assertEqual(CaptureOutcome.SUCCESS, validation.outcome)

    def test_skip_returns_manual_required_without_recollecting(self):
        collect = AsyncMock(return_value=challenge_snapshot())
        checkpoint = AsyncMock(return_value=CheckpointAction.SKIP)

        _, validation = asyncio.run(
            _validate_with_checkpoint(
                collect, ValidationPolicy(), "Hoka", "https://www.hoka.com/", checkpoint
            )
        )

        self.assertEqual(1, collect.await_count)
        self.assertEqual(CaptureOutcome.MANUAL_REQUIRED, validation.outcome)

    def test_abort_stops_capture(self):
        with self.assertRaises(CaptureAborted):
            asyncio.run(
                _validate_with_checkpoint(
                    AsyncMock(return_value=challenge_snapshot()),
                    ValidationPolicy(),
                    "Hoka",
                    "https://www.hoka.com/",
                    AsyncMock(return_value=CheckpointAction.ABORT),
                )
            )

    def test_brand_profile_is_dedicated_and_portable(self):
        profile = brand_profile_dir(Path("profiles"), "Hoka / US")
        self.assertEqual(Path("profiles"), profile.parent)
        self.assertTrue(profile.name.startswith("Hoka_US_"))
        self.assertNotEqual(
            profile, brand_profile_dir(Path("profiles"), "Hoka US")
        )


class PersistentCliTests(unittest.TestCase):
    def test_routes_persistent_configuration(self):
        result = CaptureResult(
            brand="Hoka",
            requested_url="https://www.hoka.com/",
            strategy="persistent",
            outcome=CaptureOutcome.SUCCESS,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            capture = AsyncMock(return_value=result)
            with patch.object(sitesnap_diagnose, "diagnose_url_persistent", capture):
                code = sitesnap_diagnose.main(
                    [
                        "https://www.hoka.com/",
                        "--brand",
                        "Hoka",
                        "--strategy",
                        "persistent",
                        "--browser",
                        "chrome",
                        "--user-data-root",
                        temp_dir,
                    ]
                )

        self.assertEqual(0, code)
        self.assertEqual(Path(temp_dir), capture.await_args.kwargs["user_data_root"])
        self.assertFalse(capture.await_args.kwargs["options"].headless)
