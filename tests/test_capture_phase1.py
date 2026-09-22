import json
from pathlib import Path
import tempfile
import unittest

from sitesnap_capture.artifacts import safe_artifact_name, write_failure_artifacts
from sitesnap_capture.models import CaptureOutcome, CaptureResult, PageSnapshot
from sitesnap_capture.validation import ValidationPolicy, validate_page


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ValidationTests(unittest.TestCase):
    def test_accepts_valid_brand_page(self):
        html = fixture("valid_product.html")
        snapshot = PageSnapshot(
            requested_url="https://www.example.com/shoes/trail",
            final_url="https://www.example.com/shoes/trail",
            status_code=200,
            title="Trail Running Shoes | Example",
            body_text=(
                "Explore the Example collection of cushioned trail running shoes "
                "designed for daily miles, technical terrain, and long-distance comfort. "
                "Compare current models, colors, cushioning, stability, and fit to find "
                "the right shoe for your next run. Shop the complete collection today."
            ),
            html=html,
            document_height=1800,
        )
        result = validate_page(
            snapshot,
            ValidationPolicy(
                expected_hosts=("example.com",),
                expected_text=("Example",),
            ),
        )

        self.assertEqual(CaptureOutcome.SUCCESS, result.outcome)
        self.assertTrue(result.accepted)

    def test_rejects_branded_access_denied_page(self):
        html = fixture("access_denied.html")
        snapshot = PageSnapshot(
            requested_url="https://www.example.com/",
            final_url="https://www.example.com/",
            status_code=403,
            title="Access Denied",
            body_text="Example access denied. You don't have permission to access this resource.",
            html=html,
        )

        result = validate_page(
            snapshot,
            ValidationPolicy(expected_hosts=("example.com",), expected_text=("Example",)),
        )

        self.assertEqual(CaptureOutcome.BLOCKED, result.outcome)
        self.assertFalse(result.accepted)

    def test_detects_human_challenge_before_http_error(self):
        html = fixture("challenge.html")
        snapshot = PageSnapshot(
            requested_url="https://www.example.com/",
            status_code=403,
            title="Security check",
            body_text="Verify you are human. Complete the security check to continue.",
            html=html,
            challenge_selectors=("iframe[src*=captcha]",),
        )

        result = validate_page(snapshot)

        self.assertEqual(CaptureOutcome.CHALLENGE, result.outcome)

    def test_classifies_soft_404_as_uncertain(self):
        html = fixture("soft_404.html")
        snapshot = PageSnapshot(
            requested_url="https://www.example.com/missing",
            final_url="https://www.example.com/missing",
            status_code=200,
            title="Example",
            body_text=(
                "The page you requested could not be found. Visit our homepage to continue "
                "browsing our products and stories. There are many other useful sections "
                "available from the main navigation and the site search experience."
            ),
            html=html,
            document_height=900,
        )

        result = validate_page(snapshot)

        self.assertEqual(CaptureOutcome.UNCERTAIN, result.outcome)

    def test_navigation_exception_has_dedicated_outcome(self):
        snapshot = PageSnapshot(
            requested_url="https://www.example.com/",
            navigation_error="Timeout after 60000ms",
        )

        result = validate_page(snapshot)

        self.assertEqual(CaptureOutcome.NAVIGATION_ERROR, result.outcome)


class ArtifactTests(unittest.TestCase):
    def test_writes_diagnostics_html_and_screenshot_copy(self):
        snapshot = PageSnapshot(
            requested_url="https://www.example.com/blocked",
            final_url="https://www.example.com/blocked",
            status_code=403,
            title="Access Denied",
            body_text="Access denied",
            html=fixture("access_denied.html"),
        )
        validation = validate_page(snapshot)
        result = CaptureResult(
            brand="Example Brand",
            requested_url=snapshot.requested_url,
            strategy="standard",
            outcome=validation.outcome,
            final_url=snapshot.final_url,
            status_code=snapshot.status_code,
            title=snapshot.title,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_image = root / "original.jpeg"
            source_image.write_bytes(b"test image")

            output_dir = write_failure_artifacts(
                root / "failures",
                result,
                snapshot,
                validation,
                screenshot_source=source_image,
            )

            self.assertTrue((output_dir / "page.html").exists())
            self.assertEqual(b"test image", (output_dir / "screenshot.jpeg").read_bytes())
            diagnostics = json.loads((output_dir / "diagnostics.json").read_text())
            self.assertEqual("blocked", diagnostics["capture"]["outcome"])
            self.assertFalse(diagnostics["validation"]["accepted"])
            self.assertNotIn("html", diagnostics["snapshot"])

    def test_safe_artifact_name_is_portable(self):
        self.assertEqual(
            "https_www.example.com_a_b",
            safe_artifact_name("https://www.example.com/a?b"),
        )


if __name__ == "__main__":
    unittest.main()
