
# Imports
import asyncio
import logging
import os
import pathlib
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)
from playwright_stealth import Stealth
import random
import re
import shutil
import time
from urllib.parse import urlparse

# Local imports
from sitesnap_utils import (
    print_log,
)

# Logging
logger = logging.getLogger(__name__)


# Realistic User-Agent pool (recent desktop Chrome on Windows)
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


# JS to inject into every page to harden the fingerprint
_FINGERPRINT_SPOOF_JS = """
() => {
    // ---- WebRTC leak prevention ----
    // Prevent WebRTC from exposing real local/public IPs
    if (window.RTCPeerConnection) {
        const origRTC = window.RTCPeerConnection;
        window.RTCPeerConnection = function(...args) {
            // Force all ICE traffic through the proxy by disabling local candidates
            if (args[0] && args[0].iceServers) {
                args[0].iceServers = [];
            }
            const pc = new origRTC(...args);
            const origCreateOffer = pc.createOffer.bind(pc);
            pc.createOffer = function(opts) {
                // Disable trickle ICE
                if (opts) opts.iceRestart = false;
                return origCreateOffer(opts);
            };
            return pc;
        };
        window.RTCPeerConnection.prototype = origRTC.prototype;
    }

    // ---- Canvas fingerprint noise ----
    // Add subtle random noise to canvas toDataURL / toBlob
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
        const ctx = this.getContext('2d');
        if (ctx && this.width > 0 && this.height > 0) {
            try {
                const imgData = ctx.getImageData(0, 0, Math.min(this.width, 16), 1);
                for (let i = 0; i < imgData.data.length; i += 4) {
                    imgData.data[i] ^= 1;  // flip LSB of red channel
                }
                ctx.putImageData(imgData, 0, 0);
            } catch(e) {}
        }
        return origToDataURL.call(this, type, quality);
    };

    // ---- WebGL fingerprint noise ----
    const origGetParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
        // UNMASKED_VENDOR_WEBGL
        if (param === 0x9245) return 'Google Inc. (NVIDIA)';
        // UNMASKED_RENDERER_WEBGL
        if (param === 0x9246) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0)';
        return origGetParam.call(this, param);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
        const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {
            if (param === 0x9245) return 'Google Inc. (NVIDIA)';
            if (param === 0x9246) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0)';
            return origGetParam2.call(this, param);
        };
    }

    // ---- AudioContext fingerprint noise ----
    if (window.AudioContext || window.webkitAudioContext) {
        const AC = window.AudioContext || window.webkitAudioContext;
        const origCreateOsc = AC.prototype.createOscillator;
        AC.prototype.createOscillator = function() {
            const osc = origCreateOsc.call(this);
            const origConnect = osc.connect.bind(osc);
            osc.connect = function(dest) {
                // Inject tiny gain node to add noise
                try {
                    const gain = osc.context.createGain();
                    gain.gain.value = 1.0 + (Math.random() * 0.0001 - 0.00005);
                    origConnect(gain);
                    gain.connect(dest);
                    return dest;
                } catch(e) {
                    return origConnect(dest);
                }
            };
            return osc;
        };
    }

    // ---- navigator property hardening (beyond playwright-stealth) ----
    Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
    Object.defineProperty(navigator, 'deviceMemory',        {get: () => 8});
    Object.defineProperty(navigator, 'maxTouchPoints',      {get: () => 0});
    
    // ---- Even with dom.webdriver.enabled: False, some sites check navigator.webdriver via JS ----
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

    // ---- Permissions API ----
    if (navigator.permissions) {
        const origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (params) => {
            if (params.name === 'notifications') {
                return Promise.resolve({state: 'prompt', onchange: null});
            }
            return origQuery(params);
        };
    }

    // ---- Plugin array (Chrome normally has 5 plugins) ----
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
                {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                {name: 'Native Client',     filename: 'internal-nacl-plugin'},
                {name: 'Chromium PDF Plugin', filename: 'internal-pdf-viewer'},
                {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer'},
            ];
            arr.namedItem = (n) => arr.find(p => p.name === n) || null;
            arr.refresh = () => {};
            return arr;
        }
    });
}
"""


def clear_directory(
        p: pathlib.Path,
        extensions: list[str] | None = None
) -> None:
    for filename in os.listdir(p):
        file_path = os.path.join(p, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                if extensions is None or pathlib.Path(file_path).suffix in extensions:
                    os.unlink(file_path)
        except Exception as e:
            print_log(f"\t\t\tWarning! Failed to delete {file_path}: {e}")


# ---------------------------------------------------------------------------
# Human-like behaviour helpers
# ---------------------------------------------------------------------------
async def human_pause(page: Page, multiplier: float = 1.0):
    """Random pause to mimic human reading / thinking time."""
    ms = random.randint(500, 2500) * multiplier
    await page.wait_for_timeout(int(ms))


async def human_mouse_jiggle(page: Page):
    """Move the mouse to a random spot on the page — looks organic."""
    try:
        viewport = page.viewport_size
        x = random.randint(100, viewport["width"] - 100)
        y = random.randint(100, viewport["height"] - 100)
        steps = random.randint(2, 4)
        await page.mouse.move(x, y, steps=steps)
        await page.wait_for_timeout(random.randint(100, 400))
    except Exception:
        pass


async def human_micro_scroll(page: Page):
    """Tiny scroll up/down as if the user is casually browsing."""
    try:
        delta = random.choice([-80, -50, 50, 80, 120])
        await page.mouse.wheel(0, delta)
        await page.wait_for_timeout(random.randint(200, 600))
    except Exception:
        pass


async def human_like_wait(page: Page, context: str = ""):
    """Bundle of pause + optional mouse jiggle to appear human between steps."""
    #logger.debug(f"  Human-like pause{' (' + context + ')' if context else ''}...")
    await human_pause(page)
    if random.random() < 0.6:
        await human_mouse_jiggle(page)
    if random.random() < 0.25:
        await human_micro_scroll(page)


async def launch_web_unlocker(
    pw,
    headless: bool,
) -> tuple[Browser, BrowserContext]:
    """
    Launch a local real Chrome/Edge routed through Bright Data Web Unlocker.
    """
    
    logger.info("Launching web unlocker...")
    
    # Generate a random Bright Data session ID to force a new IP
    session_id: str = f"ss{random.randint(100000, 999999)}_{int(time.time())}"
    
    # Web unlocker credentials
    WEB_UNLOCKER_USER = os.environ['WEB_UNLOCKER_USER']
    WEB_UNLOCKER_PASS = os.environ['WEB_UNLOCKER_PASS']
    WEB_UNLOCKER_HOST = os.environ['WEB_UNLOCKER_HOST']
    WEB_UNLOCKER_PORT = 44445
    
    if not WEB_UNLOCKER_USER or not WEB_UNLOCKER_PASS:
        raise RuntimeError(
            "Web Unlocker mode requires WEB_UNLOCKER_USER and WEB_UNLOCKER_PASS. "
            "The credentials come from your Web Unlocker zone in Bright Data."
        )

    username = WEB_UNLOCKER_USER
    if session_id and "-session-" not in username:
        username = f"{username}-session-{session_id}"

    proxy = {
        "server": f"http://{WEB_UNLOCKER_HOST}:{WEB_UNLOCKER_PORT}",
        "username": username,
        "password": WEB_UNLOCKER_PASS,
    }
    
    pw_context = None
    for channel in ["firefox", "chrome", "msedge", None]:
        try:
            label = channel or "bundled chromium"
            ua = random.choice(_USER_AGENTS)
            
            if channel == "firefox":
                ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/109.0"
                pw_browser = await pw.firefox.launch(
                    headless=headless,
                    proxy=proxy,
                    firefox_user_prefs={
                        # Disable WebRTC local IP leaking (equivalent to the WebRtcHideLocalIpsWithMdns flags)
                        "media.peerconnection.ice.default_address_only": True,
                        "media.peerconnection.ice.no_host": True,
                        "media.peerconnection.ice.proxy_only_if_behind_proxy": True,
                
                        # Suppress automation detection hints
                        "dom.webdriver.enabled": False,
                        "useAutomationExtension": False,
                    },
                )
            else:
                pw_browser = await pw.chromium.launch(
                    headless=headless,
                    channel=channel,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-features=WebRtcHideLocalIpsWithMdns",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        "--enforce-webrtc-ip-permission-check",
                    ],
                )
                
            pw_context = await pw_browser.new_context(
                user_agent=ua,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="America/Denver",
                ignore_https_errors=True,
                proxy=proxy,
                color_scheme="light",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "DNT": "1",
                }
            )
            pw_context._browser_ref = pw_browser
            logger.info(f"  Browser (web_unlocker): {label}")
            logger.info(f"  UA: {ua}")
            break
        except Exception as e:
            logger.info(f"  {label} not available: {e}")
            
    if pw_context is None:
        raise RuntimeError("No browser found. Install Chrome/Edge or: playwright install chromium")

    stealth = Stealth()
    await stealth.apply_stealth_async(pw_context)
    # Inject fingerprint hardening JS into every new page/frame."""
    await pw_context.add_init_script(_FINGERPRINT_SPOOF_JS)
    
    # Return
    return pw_browser, pw_context


async def get_pw_context(
    pw,
    trouble_level: int,
    headless: bool,
) -> tuple[Browser, BrowserContext]:
    
    if trouble_level == 1: 
        pw_browser = await pw.firefox.launch(headless=headless, args=["--start-maximized"])
        pw_context = await pw_browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/109.0",
        )
        pw_context._browser_ref = pw_browser
        
        return pw_browser, pw_context
    
    elif trouble_level == 2:
        # pw_browser = await pw.chromium.launch(
        #         headless=headless,
        #         channel="msedge",
        #         #channel="chrome",
        #         args=[
        #             "--disable-blink-features=AutomationControlled",
        #             "--no-sandbox",
        #             #"--start-maximized",
        #             "--disable-features=WebRtcHideLocalIpsWithMdns",
        #             "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        #             "--enforce-webrtc-ip-permission-check",
        #         ],
        #     )
        # pw_context = await pw_browser.new_context(
        #         user_agent=random.choice(_USER_AGENTS),
        #         viewport={"width": 1280, "height": 800},
        #         locale="en-US",
        #         timezone_id="America/Denver",
        #         ignore_https_errors=True,
        #         color_scheme="dark",
        #     )
        # pw_context._browser_ref = pw_browser
        
        pw_browser = await pw.firefox.launch(
            headless=headless,
            firefox_user_prefs={
                # Disable WebRTC local IP leaking (equivalent to the WebRtcHideLocalIpsWithMdns flags)
                "media.peerconnection.ice.default_address_only": True,
                "media.peerconnection.ice.no_host": True,
                "media.peerconnection.ice.proxy_only_if_behind_proxy": True,
        
                # Suppress automation detection hints
                "dom.webdriver.enabled": False,
                "useAutomationExtension": False,
            },
        )
        pw_context = await pw_browser.new_context(
            #user_agent=random.choice(_USER_AGENTS),
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/109.0",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/Denver",
            ignore_https_errors=True,
            color_scheme="light",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "DNT": "1",
            }
        )
        pw_context._browser_ref = pw_browser
        
        stealth = Stealth()
        await stealth.apply_stealth_async(pw_context)
        # Inject fingerprint hardening JS into every new page/frame."""
        await pw_context.add_init_script(_FINGERPRINT_SPOOF_JS)
        
        return pw_browser, pw_context
    
    elif trouble_level == 3:
        return await launch_web_unlocker(pw=pw, headless=headless)


async def auto_accept_cookies(
    page: Page
) -> None:
    """
    Attempts to automatically accept cookie consent pop-ups or modals.
    """
    
    # --- Handle cookie banners inside iframes (Sourcepoint, etc.) ---
    iframe_selectors = [
        'iframe[id^="sp_message_iframe"]',       # Sourcepoint
        'iframe[id^="sp_message_container"]',     # Sourcepoint alt
        '#gdpr-consent-notice',                   # generic GDPR iframe
    ]
    
    for iframe_sel in iframe_selectors:
        try:
            iframe_locator = page.locator(iframe_sel).first
            if await iframe_locator.count() == 0:
                continue
            await iframe_locator.wait_for(state="attached", timeout=3000)
    
            frame = iframe_locator.content_frame
            if frame is None:
                continue
    
            # Try accept/OK buttons inside the iframe
            iframe_button_selectors = [
                "button.sp_choice_type_Accept",           # Sourcepoint accept
                "button[title='OK']",
                "button[title='Accept']",
                "button[title='Accept All']",
                "button.button-ok",
                "button.button-close",
            ]
    
            for btn_sel in iframe_button_selectors:
                try:
                    btn = frame.locator(btn_sel).first
                    if await btn.count() == 0:
                        continue
                    await btn.wait_for(timeout=3000)
                    await btn.click()
                    logger.info(f"Clicked iframe button: '{btn_sel}' in '{iframe_sel}'")
                    return
                except Exception:
                    continue
    
        except PlaywrightTimeoutError:
            logger.info(f"Iframe '{iframe_sel}' not found!")
            continue
        except Exception as e:
            logger.info(f"Error with iframe '{iframe_sel}': {e}")
            continue
    
    # --- Direct CSS selectors for known cookie banners ---
    css_selectors = [
        "#onetrust-close-btn-container button",   # OneTrust close (X)
        "#onetrust-accept-btn-handler",           # OneTrust "Accept All"
        ".onetrust-close-btn-handler",            # OneTrust close by class
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",  # Cookiebot
        "#didomi-notice-agree-button",            # Didomi
    ]
    
    for css in css_selectors:
        try:
            locator = page.locator(css)
            if await locator.count() == 0:
                continue
            first_button = locator.first
            await first_button.wait_for(timeout=1000)
            await first_button.click()
            logger.info(f"Clicked on CSS selector: '{css}'")
            return  # Done — exit the entire function
        except PlaywrightTimeoutError:
            logger.info(f"CSS selector: '{css}' not found!")
            continue
        except Exception as e:
            logger.info(f"Error clicking CSS selector '{css}': {e}")
            continue
    
    button_id_selectors = [
        "settingsModalClose",
        "title-Close-dialog",
        "closeIconContainer",
        "dismissbutton",
        "dismissbutton2",
        "dismiss-button",
        "dismiss-button2",
        "closeIconSvg",
    ]
    
    for button_id in button_id_selectors:
        try:
            # locator = await page.get_by_role("button", id=re.compile(button_id, re.IGNORECASE))
            locator = page.locator(f'button[id*="{button_id}" i]')
            if await locator.count() == 0:
                continue
            first_button = locator.first
            await first_button.wait_for(timeout=1000)
            await first_button.click()
            
            logger.info(f"Clicked on button: '{button_id}'")
            # Exit after clicking to avoid multiple pop-ups
            break
        except PlaywrightTimeoutError:
            # If the selector is not found, continue to the next one
            logger.info(f"button: '{button_id}' not found!")
            continue
        except Exception as e:
            logger.info(f"An error occurred while clicking button '{button_id}': {e}")
            continue
    
    button_name_selectors = [
        "Accept",
        "I Accept",
        "Agree",
        "I Agree",
        "Accept Cookies",
        "Accept All Cookies",
        "Got it",
        "Allow All",
        "Confirm My Choices",
        "OK",
        "Continue",
        "Close Dialog",
        "Close",
        "Decline Offer",
        "No Thanks",
        "Dismiss This Popup",
        #"Go",
        "Decline Offer",
        "Save And Exit",
    ]
    
    for button_name in button_name_selectors:
        try:
            
            # Old way
            #locator = page.get_by_role("button", name=re.compile(button_name, re.IGNORECASE))
            
            # New way: whole-string, case-insensitive, tolerant of surrounding whitespace
            pattern = re.compile(rf"^\s*{re.escape(button_name)}\s*$", re.IGNORECASE)
            locator = page.get_by_role("button", name=pattern)
            
            if await locator.count() == 0:
                continue
            first_button = locator.first
            await first_button.wait_for(timeout=1000)
            await first_button.click()
            
            logger.info(f"Clicked on button: '{button_name}'")
            # Exit after clicking to avoid multiple pop-ups
            break
        except PlaywrightTimeoutError:
            # If the selector is not found, continue to the next one
            logger.info(f"button: '{button_name}' not found!")
            continue
        except Exception as e:
            logger.info(f"An error occurred while clicking button '{button_name}': {e}")
            continue
    
    data_click_selectors = [
        "close",
    ]
    
    for button_data_click in data_click_selectors:
        try:
            
            #locator = page.locator(f'button[data-click="{re.compile(button_data_click, re.IGNORECASE)}"]')
            locator = page.locator(f'button[data-click="{button_data_click}" i]')
            if await locator.count() == 0:
                continue
            first_button = locator.first
            await first_button.wait_for(timeout=1000)
            await first_button.click()
            
            logger.info(f"Clicked on button: '{button_data_click}'")
            # Exit after clicking to avoid multiple pop-ups
            break
        except PlaywrightTimeoutError:
            # If the selector is not found, continue to the next one
            logger.info(f"button: '{button_data_click}' not found!")
            continue
        except Exception as e:
            logger.info(f"An error occurred while clicking button '{button_data_click}': {e}")
            continue


async def scroll_page(
    page: Page,
    direction: str,
    max_height: int = 30_000,
) -> Page:
    
    # Which direction?
    if direction == "down":
        keyboardPress = "PageDown"
        scrollDir = 1
    elif direction == "up":
        keyboardPress = "PageUp"
        scrollDir = -1
    
    # ESC Key
    await page.keyboard.press('Escape')
    await asyncio.sleep(random.uniform(0.5, 1.5))
    
    # Scrolling all the way up/down
    prev_box = await page.locator("body").bounding_box()
    for i in range(20):
        
        await page.keyboard.press(keyboardPress)
        await page.wait_for_timeout(random.randint(250, 1000))
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        await page.keyboard.press('Escape')
        await page.wait_for_timeout(random.randint(250, 1000))
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        await page.mouse.wheel(0, scrollDir * random.randint(500, 1500))
        await page.wait_for_timeout(random.randint(250, 1000))
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        # If page stopped moving, then break
        new_box = await page.locator("body").bounding_box()
        if new_box["y"] == prev_box["y"] or new_box["y"] < (-1 * max_height):
            break
        
        # Else continue on
        prev_box = new_box
    
    return page


async def capture_screenshot(
    url: str,
    pw_page: Page,
    image_dir: pathlib.Path,
    archive_dir: pathlib.Path,
    file_name: str,
    max_height: int = 30_000,
    image_type: str = "jpeg", # png or jpeg
) -> None:
    
    logger.info(f"  Navigating to {url} ...")
    try:
        await pw_page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        logger.info(f"  Navigation warning: {e}")
    
    # # refresh
    # await asyncio.sleep(10)
    # logger.info("  Refreshing ...")
    # await pw_page.keyboard.press("F5")
    # await asyncio.sleep(10)
    
    # Wait for network
    logger.info("  Waiting for network to settle...")
    try:
        await pw_page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        logger.info("  Network didn't fully idle — continuing")
    
    # Human-like: pause as if reading the page, jiggle mouse
    await human_like_wait(pw_page, "initial page load")
    await human_pause(pw_page, multiplier=1.5)
    
    # Wait for the challenge cookie to appear before proceeding
    logger.info("  Waiting for challenge cookie...")
    try:
        await pw_page.wait_for_function("""
            () => document.cookie.includes('ak_bmsc') || 
                  document.cookie.includes('_cf_bm') ||
                  document.cookie.includes('reese84')
        """, timeout=10000)
    except Exception:
        logger.info(" Cookie didn't appear — continuing")
    
    # Check for lazy loading by scrolling all the way down
    pw_page = await scroll_page(page=pw_page, direction="down", max_height=max_height)
    
    # Attempt to accept cookies #1
    await auto_accept_cookies(pw_page)
    
    # Continue scrolling down
    pw_page = await scroll_page(page=pw_page, direction="down", max_height=max_height)
    
    # Attempt to accept cookies #2
    await auto_accept_cookies(pw_page)
    
    # Scroll up #1
    #pw_page = await scroll_page(page=pw_page, direction="up", max_height=max_height)
    await pw_page.keyboard.press('Control+Home')
    
    # ESC Key
    await pw_page.keyboard.press('Escape')
    await asyncio.sleep(random.uniform(0.5, 1.5))
    
    # Determine screenshot size
    try:
        page_height = await pw_page.evaluate("document.body.scrollHeight")
        page_width = await pw_page.evaluate("document.body.scrollWidth")
    except Exception:
        box = await pw_page.locator("body").bounding_box()
        page_height = int(box["height"]) if box else 15000
        page_width = int(box["width"]) if box else 1280
    #print(f"h={page_height}, w={page_width}")
    
    logger.info("  Taking screenshot...")
    if page_height > max_height:
        # Resize viewport max height, then screenshot, then revert back to old viewport
        original_viewport = pw_page.viewport_size or {"width": 1280, "height": 800}
        await pw_page.set_viewport_size({"width": page_width, "height": max_height})
        await pw_page.screenshot(
            path=pathlib.Path(image_dir, file_name + f".{image_type}"),
            animations="disabled",
            full_page=False,
            type=image_type,
            timeout=120000,
        )
        await pw_page.set_viewport_size(original_viewport)
        
    else:
        # Normal screenshot without resizing
        await pw_page.screenshot(
            path=pathlib.Path(image_dir, file_name + f".{image_type}"),
            animations="disabled",
            full_page=True,
            type=image_type,
            timeout=120000,
        )
    
    # Make copy in archive folder
    shutil.copy(pathlib.Path(image_dir, file_name + f".{image_type}"),
                pathlib.Path(archive_dir, file_name + f".{image_type}"))


async def get_snaps_normal(
    urls: list[str],
    trouble_level: int,
    image_dir: pathlib.Path,
    archive_dir: pathlib.Path,
    headless: bool = False,
    image_type: str = "jpeg", # png or jpeg
    delay: int = 2,
    max_height: int = 30_000,
) -> None:
    
    # Initialize playwright
    async with async_playwright() as pw:
        
        # Launch browser and context
        pw_browser, pw_context = await get_pw_context(
            pw=pw,
            trouble_level=trouble_level,
            headless=headless,
        )
        pw_page = pw_context.pages[0] if pw_context.pages else await pw_context.new_page()
        
        # Attempt at a "pop-up guard"
        pw_page.on("popup", lambda p: asyncio.create_task(p.close()))
        
        
        # Go to random page first
        try:
            await pw_page.goto("https://www.duckduckgo.com", timeout=5000, wait_until="commit")
        except:
            pass
        
        # Loop over urls
        for url in urls:
            
            # Delay between URLs
            await asyncio.sleep(delay)
            
            # First check if URL is valid
            parsed_url = urlparse(url)
            if not (bool(parsed_url.netloc) and bool(parsed_url.scheme)):
                print_log(f"\t\t\tWarning: The following url is not valid: {url}")
                continue
            
            # Determine a good file name for snap
            file_name = parsed_url.netloc + parsed_url.path.replace('/', '_') + parsed_url.fragment
            file_name = file_name.replace('.', '_').strip('_')
            file_name = file_name.replace('#', '')
            
            try:
                # # Navigate to the URL
                # await pw_page.goto(url, timeout=120000, wait_until="commit")
                # await asyncio.sleep(10)
                
                # Attempt to take screenshot
                await capture_screenshot(
                    url=url,
                    pw_page=pw_page,
                    image_dir=image_dir,
                    archive_dir=archive_dir,
                    file_name=file_name,
                    max_height=max_height,
                    image_type=image_type,
                )
                
            except Exception as e:
                print_log(f"\t\t\tError viewing {url}: {e}")
            
            # Delay before continuing to next URL
            await asyncio.sleep(10)
        
        # Close out browser and context
        await pw_context.close()
        await pw_browser.close()


async def get_snaps_web_unlocker(
    urls: list[str],
    trouble_level: int,
    image_dir: pathlib.Path,
    archive_dir: pathlib.Path,
    headless: bool = False,
    image_type: str = "jpeg", # png or jpeg
    delay: int = 2,
    max_height: int = 30_000,
) -> None:
    
    # Loop over urls
    for url in urls:
        logger.info(f"Start of attempts for: {url}")
        
        # Delay between URLs
        await asyncio.sleep(delay)
        
        # First check if URL is valid
        parsed_url = urlparse(url)
        if not (bool(parsed_url.netloc) and bool(parsed_url.scheme)):
            print_log(f"\t\t\tWarning: The following url is not valid: {url}")
            continue
        
        # Determine a good file name for snap
        file_name = parsed_url.netloc + parsed_url.path.replace('/', '_') + parsed_url.fragment
        file_name = file_name.replace('.', '_').strip('_')
        file_name = file_name.replace('#', '')
        
        # Initialize playwright
        async with async_playwright() as pw:
            
            num_retries: int = 5
            for attempt in range(1, num_retries + 1):
                success: bool = False
                
                # Launch browser and context
                pw_browser, pw_context = await get_pw_context(
                    pw=pw,
                    trouble_level=trouble_level,
                    headless=headless,
                )
                pw_page = pw_context.pages[0] if pw_context.pages else await pw_context.new_page()
                
                # Attempt at a "pop-up guard"
                pw_page.on("popup", lambda p: asyncio.create_task(p.close()))
                
                # Go to random page first
                try:
                    await pw_page.goto("https://www.duckduckgo.com", timeout=5000, wait_until="commit")
                except:
                    pass
                
                try:
                    # Navigate to the URL
                    #await pw_page.goto(url, timeout=120000, wait_until="commit")
                    #await asyncio.sleep(60)
                    
                    # Attempt to take screenshot
                    await capture_screenshot(
                        url=url,
                        pw_page=pw_page,
                        image_dir=image_dir,
                        archive_dir=archive_dir,
                        file_name=file_name,
                        max_height=max_height,
                        image_type=image_type,
                    )
                    
                    # Success
                    success = True
                    
                except Exception as e:
                    print_log(f"\t\t\tError (attempt #{attempt})\n\t\t\t{url}\n\t\t\t{e}")
                    if attempt < num_retries:
                        backoff_sec = 5 * (2 ** (attempt - 1)) + random.uniform(1, 3)
                        print_log(f"Backing off {backoff_sec:.1f}s before retry...")
                        await asyncio.sleep(backoff_sec)
                
                finally:
                    # Close out browser and context
                    await pw_context.close()
                    await pw_browser.close()
                
                # If success, break
                if success:
                    break
            
            if success == False:
                print_log(f"\t\t\tFAILED AFTER ALL ATTEMPTS\n\t\t\t{url}")


async def get_snaps(
    urls: list[str],
    image_dir: pathlib.Path,
    archive_dir: pathlib.Path,
    trouble_level: int,
    headless: bool = False,
    image_type: str = "jpeg", # png or jpeg
    delay: int = 2,
    max_height: int = 30_000,
) -> None:
    
    if trouble_level == 3:
        await get_snaps_web_unlocker(
            urls=urls,
            trouble_level=trouble_level,
            image_dir=image_dir,
            archive_dir=archive_dir,
            headless=headless,
            image_type=image_type,
            delay=delay,
            max_height=max_height,
        )
    
    else:
        await get_snaps_normal(
            urls=urls,
            trouble_level=trouble_level,
            image_dir=image_dir,
            archive_dir=archive_dir,
            headless=headless,
            image_type=image_type,
            delay=delay,
            max_height=max_height,
        )

