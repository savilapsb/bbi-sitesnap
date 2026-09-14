import asyncio
from playwright.async_api import async_playwright

PROXY = {
    "server": "http://brd.superproxy.io:33335",
    "username": "brd-customer-hl_a5164a4a-zone-residential_proxy1-session-test01",
    "password": "YBwnMQ3nLwyTc6zpsJrf",
}

URL = "https://www.hoka.com"

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=r"C:\sitesnap-profile-hoka",
            channel="chrome",
            headless=False,
            no_viewport=True,
            proxy=PROXY,
            ignore_https_errors=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await ctx.new_page()
        r = await page.goto(URL, timeout=120000)
        print("status:", r.status if r else None, "| len:", len(await page.content()))
        input("Solve anything on screen if needed, then press Enter...")
        await page.screenshot(path="hoka_test.png", full_page=True)
        await ctx.close()

asyncio.run(main())