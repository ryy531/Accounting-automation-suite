"""
Diagnostic: logs in, enters a test entity number, and prints what appears
on the page so we can identify the correct form option selectors.
"""
import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

ACCOUNT_FILE = r"\\file-server\Shared Folder\Scripts\AccessibilityCheck\Account.txt"
SITE_URL     = "https://corporateregistry.isc.ca"

# ← Put a real SK entity number here to test with
TEST_ENTITY  = "101234570"

def read_account():
    creds = {}
    with open(ACCOUNT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                creds[key.strip().lower()] = val.strip()
    return creds

async def main():
    creds = read_account()

    if not TEST_ENTITY:
        print("\nERROR: Set TEST_ENTITY in diagnose.py to a real SK entity number first.\n")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page    = await browser.new_page()

        # ── Login ──────────────────────────────────────────────────────────────
        await page.goto(SITE_URL, wait_until="domcontentloaded")
        inputs = await page.locator("input[type='text'], input[type='password']").all()
        if len(inputs) >= 2:
            await inputs[0].fill(creds.get("username", ""))
            await inputs[1].fill(creds.get("password", ""))
        elif len(inputs) == 1:
            await inputs[0].fill(creds.get("username", ""))
        await page.get_by_role("button", name="login", exact=False).first.click()
        await page.wait_for_function(
            "() => !document.querySelector('#UserLoginName')",
            timeout=15000,
        )
        await page.wait_for_load_state("domcontentloaded")
        print("  Logged in.\n")

        # ── Enter entity number ────────────────────────────────────────────────
        print(f"  Entering entity number: {TEST_ENTITY}")
        field = page.locator("#EntityNumber")
        await field.wait_for(state="visible", timeout=5000)
        await field.fill(TEST_ENTITY)

        # Wait for autocomplete dropdown
        await page.wait_for_timeout(1500)

        print("\n=== Autocomplete dropdown options ===\n")
        for sel in ["ul[role='listbox'] li", ".ui-autocomplete li", ".dropdown-menu li",
                    ".autocomplete-suggestion", "[class*='autocomplete'] li"]:
            items = await page.locator(sel).all()
            if items:
                print(f"  Selector: {sel}")
                for i, item in enumerate(items):
                    txt = (await item.inner_text()).strip()
                    visible = await item.is_visible()
                    print(f"    [{i}] visible={visible}  text={repr(txt)}")
                break
        else:
            print("  (no dropdown found — trying Enter)")
            await field.press("Enter")
            await page.wait_for_load_state("domcontentloaded")

        input("\n  ↑ Check the browser. Press Enter here to click the first dropdown item…\n")

        # Click the first visible dropdown item
        for sel in ["ul[role='listbox'] li", ".ui-autocomplete li", ".dropdown-menu li",
                    ".autocomplete-suggestion", "[class*='autocomplete'] li"]:
            items = await page.locator(sel).all()
            if items:
                await items[0].click()
                break
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1000)

        # ── Print all visible links/buttons on the resulting page ──────────────
        print("\n=== Visible links on entity page ===\n")
        links = await page.locator("a, button").all()
        for i, el in enumerate(links):
            visible = await el.is_visible()
            if not visible:
                continue
            txt = (await el.inner_text()).strip()
            if txt:
                print(f"  [{i}] <{await el.evaluate('e => e.tagName').lower() if False else 'el'}>  {repr(txt)}")

        print("\n=== Page text snippet (first 3000 chars) ===\n")
        body_text = await page.locator("body").inner_text()
        print(body_text[:3000])

        input("\nPress Enter to close the browser…")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
