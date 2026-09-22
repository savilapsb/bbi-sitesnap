# SiteSnap

Captures screenshots of the most important pages on each BBI brand's website, for use as input to site dimension / emotion / CEP coding.

> Converted to Markdown from Michael's original `ReadMe.docx` (dated 2026-07-16). Content is unchanged apart from formatting. Two inline images from the original (the Edge "…" menu and the Save icon) did not survive conversion; the original `.docx` is kept alongside this file.

## Scripts

### Main scripts

- `sitesnap_get_sites.py`
- `sitesnap_get_snaps.py`

### Support modules

- `sitesnap_utils.py`
- `sitesnap_site_functions.py`
- `sitesnap_snap_functions.py`
- `sitesnap_thread_functions.py`
- `APITracker.py`
- `APIRequest.py`
- `APIBatch.py`

### Basic script hierarchy

```
sitesnap_get_sites.py
    └── sitesnap_site_functions.py

sitesnap_get_snaps.py
    └── sitesnap_snap_functions.py
```

## Main scripts

### `sitesnap_get_sites.py`

- Searches the web to determine the most important sub-pages within a domain for each brand.
- Outputs two files to each brand directory (`/brand_data/[brand]/[year]/[month]/sitesnap/`):
  - `[brand] Important Links.xlsx`
  - `[brand]_links.json`

### `sitesnap_get_snaps.py`

- Automatically browses to the webpages found in step A, scrolls down to the bottom of the page (to load all assets), tries to close out any pop-ups or cookie modals, moves back to the top of the page, and then takes a screenshot.
- Reads the `[brand]_links.json` from step A to determine the correct links to visit.
- Saves the screenshots to each brand directory (`/brand_data/[brand]/[year]/[month]/sitesnap/`).
- `trouble_list` contains a list of brand websites that have made it hard to take screenshots:
  - If a brand is **not** on this list, then normal code + browser works, zero trouble.
  - If trouble value = `2`, then this brand requires extra browser settings, like `playwright_stealth` and special JavaScript.
  - If trouble value = `3`, then this brand requires Bright Data's Web Unlocker. Sometimes the Web Unlocker settings make the connection very slow and pages might not fully load for screenshots.
  - If trouble value = `None`, then this brand is beyond help and requires manual screenshots.

## How to take manual screenshots

1. Within each brand's sitesnap directory (`/brand_data/[brand]/[year]/[month]/sitesnap/`) should be the `[brand]_links.json` file from step A. Open that to find the list of URLs that need screenshots for that brand.
2. Use the MS Edge browser and paste in the needed URL. Scroll down to the bottom of the page to make sure everything gets loaded. Go back to the top of the page. Make sure to exit out of any pop-up or modal windows (e.g. asking about cookies).
3. Click the "…" options button at the top right of the browser, then choose the **Screenshot** option.
4. Click **Capture Full Page**. This should open a new window with options to edit the screenshot. Click the **Save** icon at the top right. This will autosave the screenshot to your **Downloads** folder.
5. Do this for all URLs for the current brand. When completed, select and cut all of the screenshots from the **Downloads** folder and paste them into the brand's sitesnap directory (`/brand_data/[brand]/[year]/[month]/sitesnap/`).

> **Tip (added 2026-09):** Chrome's DevTools can do the same thing. Open DevTools, press `Ctrl+Shift+P`, type `screenshot`, and choose **Capture full size screenshot**. Scroll to the bottom first so lazy-loaded content is present.

## Capture engine v2 development

Work on the replacement screenshot engine lives in `sitesnap_capture/`. Phase 1
adds shared result models, deterministic page validation, and failure-artifact
persistence. It intentionally does not change the production entry point yet;
`sitesnap_get_snaps.py` continues to use the legacy capture implementation while
the new engine is developed and tested.

The validator distinguishes successful pages from access-denied responses,
human-verification challenges, empty renders, navigation failures, and uncertain
pages such as soft 404s. Rejected captures can be stored with their rendered HTML,
screenshot, and a machine-readable `diagnostics.json` file so that failures can be
investigated without rerunning the site.

Run the Phase 1 tests from the repository root with:

```bash
python -m unittest discover -s tests -v
```

### Single-page browser diagnostics

Phase 2 adds `sitesnap_diagnose.py`, an isolated command for opening one URL in
an unmodified Chromium-family browser. It records the final URL, main-document
status, title, visible-content size, document dimensions, image counts, browser
console errors, failed requests, and challenge indicators before applying the
Phase 1 validator. This command does not use a proxy, stealth package, user-agent
override, or fingerprint script.

Install Playwright and its bundled Chromium browser if they are not already
available in the Python environment:

```bash
python -m pip install playwright
python -m playwright install chromium
```

Run a visible diagnostic capture with:

```bash
python sitesnap_diagnose.py https://www.hoka.com/ \
    --brand Hoka \
    --browser chromium \
    --expected-host hoka.com \
    --expected-text HOKA
```

On Windows PowerShell, place the command on one line or use PowerShell's backtick
line-continuation character instead of `\`. Use `--browser chrome` or
`--browser msedge` to launch an installed browser channel, and add `--headless`
only when a visible browser is not needed.

Successful captures are written to `runs/diagnostics/` with an adjacent
`*.diagnostics.json` report. Rejected captures are moved under
`runs/diagnostics/failures/` with their screenshot, rendered HTML, and diagnostic
report. Exit code `0` means the page passed validation, `2` means the page was
captured but rejected, and `1` means the command itself could not run.

### Persistent operator-assisted capture (Phase 3)

For a site that presents human verification to a fresh automated browser, use
the persistent strategy with an installed Google Chrome or Microsoft Edge. It
runs headed and stores cookies and other browser state in a dedicated per-brand
directory under `runs/browser-profiles/` (override with `--user-data-root`). Do
not share one brand's profile with another brand.

```bash
python sitesnap_diagnose.py https://www.hoka.com/ \
    --brand Hoka \
    --strategy persistent \
    --browser chrome \
    --expected-host hoka.com \
    --expected-text HOKA
```

If validation detects a challenge, the browser remains open and the terminal
offers **Continue**, **Skip**, or **Abort**. Complete any verification yourself
in the visible browser. Continue recollects the rendered page and reruns all
validation; it never accepts the pre-checkpoint page. If a challenge remains,
the prompt repeats. Skip records a `manual_required` failure without creating an
accepted screenshot, and Abort stops with exit code `130`. A successful profile
is reused on later runs for that brand.

This workflow deliberately does **not** solve CAPTCHAs, spoof browser
fingerprints, install stealth scripts, or route traffic through residential
proxies. Persistent mode rejects `--headless` and bundled `chromium`; choose
`--browser chrome` or `--browser msedge`. Close other processes using the same
profile directory before starting a capture.

#### If verification becomes “Access is temporarily restricted”

That result is a site-side rejection, not a successful CAPTCHA completion. Use
**Abort** (or **Skip** if you want failure artifacts); do not repeatedly retry
the challenge. First open the same URL in ordinary Chrome or Edge on the same
machine, outside SiteSnap. If that is also restricted, the device or network is
being rejected and changing SiteSnap will not resolve it. A VM—especially one
using a corporate, VPN, cloud, or datacenter egress IP—may be treated differently
from a physical laptop on its normal network, so testing the ordinary browser
and then SiteSnap on the laptop is a useful comparison.

On Windows, persistent mode keeps Chrome's sandbox enabled and removes
Playwright's unnecessary `--no-sandbox` default argument. The Chrome warning
about an unsupported `--no-sandbox` flag should therefore not appear. This is a
security correction, not an attempt to disguise automation, and a site may
still decline the browser session. Do not work around that decision with rapid
retries, fingerprint spoofing, CAPTCHA automation, or proxy rotation.
