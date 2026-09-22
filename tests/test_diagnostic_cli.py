import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import sitesnap_diagnose
from sitesnap_capture.browser import _collect_snapshot, playwright_available
from sitesnap_capture.models import CaptureOutcome, CaptureResult


class FakeLocator:
    def __init__(self, count=0, text=""):
        self._count = count
        self._text = text

    async def count(self):
        return self._count

    async def inner_text(self, timeout):
        return self._text


class FakePage:
    url = "https://www.example.com/products"

    async def title(self):
        return "Products | Example"

    async def content(self):
        return "<html><body>Example product content</body></html>"

    async def evaluate(self, script):
        return {"width": 1280, "height": 2400, "images": 5, "loaded_images": 4}

    def locator(self, selector):
        if selector == "body":
            return FakeLocator(text="Example product content for customers")
        if selector == "#px-captcha":
            return FakeLocator(count=1)
        return FakeLocator()


class BrowserCollectionTests(unittest.TestCase):
    def test_collect_snapshot_records_browser_observations(self):
        snapshot = asyncio.run(
            _collect_snapshot(
                page=FakePage(),
                requested_url="https://www.example.com/products",
                status_code=200,
                console_errors=["script failed"],
                failed_requests=["GET image.jpg: net::ERR_FAILED"],
                navigation_error=None,
            )
        )

        self.assertEqual(200, snapshot.status_code)
        self.assertEqual(2400, snapshot.document_height)
        self.assertEqual(5, snapshot.image_count)
        self.assertEqual(("#px-captcha",), snapshot.challenge_selectors)
        self.assertEqual(("script failed",), snapshot.console_errors)

    def test_playwright_availability_returns_boolean(self):
        self.assertIsInstance(playwright_available(), bool)


class DiagnosticCliTests(unittest.TestCase):
    def test_rejects_incomplete_url(self):
        exit_code = sitesnap_diagnose.main(["example.com", "--brand", "Example"])
        self.assertEqual(1, exit_code)

    def test_passes_cli_configuration_to_browser_strategy(self):
        result = CaptureResult(
            brand="Example",
            requested_url="https://www.example.com/",
            strategy="standard",
            outcome=CaptureOutcome.SUCCESS,
        )
        mock_diagnose = AsyncMock(return_value=result)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(sitesnap_diagnose, "diagnose_url", mock_diagnose):
                exit_code = sitesnap_diagnose.main(
                    [
                        "https://www.example.com/",
                        "--brand",
                        "Example",
                        "--output-dir",
                        temp_dir,
                        "--browser",
                        "chrome",
                        "--headless",
                        "--expected-text",
                        "Example",
                        "--navigation-timeout",
                        "30",
                    ]
                )

        self.assertEqual(0, exit_code)
        call = mock_diagnose.await_args.kwargs
        self.assertEqual(Path(temp_dir), call["output_dir"])
        self.assertEqual("chrome", call["options"].browser)
        self.assertTrue(call["options"].headless)
        self.assertEqual(30_000, call["options"].navigation_timeout_ms)
        self.assertEqual(("www.example.com",), call["policy"].expected_hosts)
        self.assertEqual(("Example",), call["policy"].expected_text)

    def test_returns_two_for_rejected_page(self):
        result = CaptureResult(
            brand="Example",
            requested_url="https://www.example.com/",
            strategy="standard",
            outcome=CaptureOutcome.BLOCKED,
        )
        with patch.object(
            sitesnap_diagnose,
            "diagnose_url",
            AsyncMock(return_value=result),
        ):
            exit_code = sitesnap_diagnose.main(
                ["https://www.example.com/", "--brand", "Example"]
            )

        self.assertEqual(2, exit_code)


if __name__ == "__main__":
    unittest.main()
