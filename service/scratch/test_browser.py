import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        # Launch browser headlessly, ignore SSL certificates since it is localhost self-signed
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()

        print("Navigating to login page...")
        try:
            await page.goto("https://localhost:8501/", timeout=15000)
            
            # Fill login form
            await page.fill("input[type='text']", "admin")
            await page.fill("input[type='password']", "admin123")
            await page.click("button:has-text('Đăng nhập')")
            
            # Wait for redirect
            await page.wait_for_selector(".sidebar-nav-item[data-page='dashboard']", timeout=10000)
            
            # Click dashboard
            await page.click(".sidebar-nav-item[data-page='dashboard']")
            await page.wait_for_selector("#chart-labels", timeout=10000)
            
            # Scroll down to see Phân bổ nhãn
            await page.evaluate("window.scrollTo(0, 600)")
            await page.wait_for_timeout(2000)
            
            # Take screenshot of Dashboard lower part
            await page.screenshot(path="scratch/dashboard_lower_screenshot.png")
            print("Dashboard lower screenshot saved to scratch/dashboard_lower_screenshot.png")
            
        except Exception as e:
            print("Failed during playwright test:", e)
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
