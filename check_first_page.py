import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            proxy={"server": "http://138.249.26.253:6085",
                   "username": "user409265", "password": "y41xol"},
        )
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        )
        await page.goto("https://italyvms.com/autoform/?lang=ru", timeout=30000)
        await page.wait_for_load_state("networkidle")
        await page.screenshot(path="C:\\vfs_bot\\first_page.png", full_page=True)
        print("Screenshot saved!")
        
        # Выводим все элементы формы
        elements = await page.query_selector_all("input, select, button, img")
        for el in elements:
            tag = await el.evaluate("e => e.tagName")
            name = await el.get_attribute("name") or ""
            type_ = await el.get_attribute("type") or ""
            src = await el.get_attribute("src") or ""
            print(f"{tag} name={name} type={type_} src={src[:50]}")
        
        input("Press Enter to close...")
        await browser.close()

asyncio.run(run())
