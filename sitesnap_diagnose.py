"""Command-line diagnostic capture for a single SiteSnap URL."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from sitesnap_capture.browser import BrowserOptions, diagnose_url
from sitesnap_capture.persistent import CaptureAborted, diagnose_url_persistent
from sitesnap_capture.validation import ValidationPolicy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open one URL in a standard browser and diagnose the rendered page.",
    )
    parser.add_argument("url", help="Complete HTTP or HTTPS URL to diagnose.")
    parser.add_argument("--brand", required=True, help="Brand label for reports.")
    parser.add_argument(
        "--strategy",
        choices=("standard", "persistent"),
        default="standard",
        help="Use standard diagnostics or a persistent operator-assisted profile.",
    )
    parser.add_argument(
        "--user-data-root",
        type=Path,
        default=Path("runs", "browser-profiles"),
        help="Root for dedicated per-brand profiles used by persistent strategy.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs", "diagnostics"),
        help="Directory for screenshots and diagnostic artifacts.",
    )
    parser.add_argument(
        "--browser",
        choices=("chromium", "chrome", "msedge"),
        default="chromium",
        help="Chromium build or installed browser channel to launch.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without a visible browser window. The default is headed.",
    )
    parser.add_argument(
        "--expected-host",
        action="append",
        default=[],
        help="Allowed final hostname; may be repeated. Defaults to the URL hostname.",
    )
    parser.add_argument(
        "--expected-text",
        action="append",
        default=[],
        help="Expected brand text; may be repeated.",
    )
    parser.add_argument("--navigation-timeout", type=int, default=60)
    parser.add_argument("--settle-timeout", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    parsed_url = urlparse(args.url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
        print("ERROR: url must be a complete HTTP or HTTPS URL.", file=sys.stderr)
        return 1

    expected_hosts = tuple(args.expected_host or [parsed_url.hostname])
    policy = ValidationPolicy(
        expected_hosts=expected_hosts,
        expected_text=tuple(args.expected_text),
    )
    options = BrowserOptions(
        browser=args.browser,
        headless=args.headless,
        navigation_timeout_ms=args.navigation_timeout * 1_000,
        settle_timeout_ms=args.settle_timeout * 1_000,
    )

    try:
        capture = diagnose_url_persistent if args.strategy == "persistent" else diagnose_url
        kwargs = dict(
            url=args.url,
            brand=args.brand,
            output_dir=args.output_dir,
            policy=policy,
            options=options,
        )
        if args.strategy == "persistent":
            kwargs["user_data_root"] = args.user_data_root
        result = asyncio.run(capture(**kwargs))
    except CaptureAborted as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.outcome.value == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
