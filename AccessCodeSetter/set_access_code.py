"""
ISC Corporate Registry - Access Code Setter
Batch sets access code for all "Pending Activation" companies.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ── Config ─────────────────────────────────────────────────────────────────────
ACCOUNT_FILE = r"\\file-server\Shared Folder\Scripts\AccessibilityCheck\Account.txt"
SITE_URL     = "https://corporateregistry.isc.ca"
PROFILE_URL  = "https://corporateregistry.isc.ca/User/Profile/#AccessCodes"
ACCESS_CODE  = "Client2017"
HEADLESS     = False

BOLD  = "\033[1m"
GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"


def read_account() -> dict:
    creds = {}
    with open(ACCOUNT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                creds[key.strip().lower()] = val.strip()
    return creds


# ── Steps ──────────────────────────────────────────────────────────────────────

async def login(page, creds: dict):
    await page.goto(SITE_URL, wait_until="domcontentloaded")
    inputs = await page.locator("input[type='text'], input[type='password']").all()
    if len(inputs) >= 2:
        await inputs[0].fill(creds["username"])
        await inputs[1].fill(creds["password"])
    await page.get_by_role("button", name="login", exact=False).first.click()
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2000)


async def go_to_access_codes(page):
    await page.goto(PROFILE_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    tab = page.get_by_text("Entity Access Code", exact=False).first
    try:
        if await tab.is_visible(timeout=5000):
            await tab.click()
            await page.wait_for_timeout(1500)
    except PlaywrightTimeoutError:
        pass


async def sort_by_status(page) -> bool:
    th_elements = page.locator("th")
    count = await th_elements.count()
    for i in range(count):
        text = await th_elements.nth(i).inner_text()
        if "status" in text.lower():
            status_th = th_elements.nth(i)
            link = status_th.locator("a").first
            try:
                if await link.is_visible(timeout=2000):
                    await link.click()
                    await page.wait_for_timeout(1000)
                    await link.click()
                    await page.wait_for_timeout(1500)
                    return True
            except PlaywrightTimeoutError:
                pass
            await status_th.click()
            await page.wait_for_timeout(1000)
            await status_th.click()
            await page.wait_for_timeout(1500)
            return True
    return False


async def find_first_pending(page):
    rows = page.locator("table tbody tr")
    count = await rows.count()
    for i in range(count):
        row = rows.nth(i)
        text = await row.inner_text()
        if "pending activation" in text.lower():
            return row, i
    return None, -1


async def process_one(page) -> str | None:
    """Process one Pending Activation company. Returns company name or None."""
    # Sort
    if not await sort_by_status(page):
        print(f"  {RED}Status header not found.{RESET}")
        return None

    # Find pending row
    row, idx = await find_first_pending(page)
    if row is None:
        return None

    row_text = await row.inner_text()
    company = row_text.split("\t")[2].strip() if "\t" in row_text else row_text[:60]
    print(f"  Processing: {company}", end="", flush=True)

    # Select checkbox
    checkbox = row.locator("input[type='checkbox']").first
    try:
        await checkbox.wait_for(state="visible", timeout=5000)
        await checkbox.click()
    except PlaywrightTimeoutError:
        await row.click()
    await page.wait_for_timeout(500)

    # Click Set Code
    for label in ["Set Code", "Set code", "SET CODE"]:
        btn = page.get_by_role("button", name=label, exact=False).first
        try:
            if await btn.is_visible(timeout=2000):
                await btn.click()
                break
        except PlaywrightTimeoutError:
            continue
    await page.wait_for_timeout(1500)

    # Find code inputs (not disabled, visible)
    all_inputs = page.locator(
        "input[type='text']:not([disabled]), "
        "input[type='password']:not([disabled]), "
        "input:not([type]):not([disabled])"
    )
    code_keywords = ["access", "code", "password", "confirm", "new"]
    code_inputs = []
    count = await all_inputs.count()
    for i in range(count):
        inp = all_inputs.nth(i)
        try:
            if not await inp.is_visible(timeout=300):
                continue
            name = (await inp.get_attribute("name") or "").lower()
            id_  = (await inp.get_attribute("id") or "").lower()
            ph   = (await inp.get_attribute("placeholder") or "").lower()
            for kw in code_keywords:
                if kw in name or kw in id_ or kw in ph:
                    code_inputs.append(inp)
                    break
        except Exception:
            pass

    if len(code_inputs) >= 2:
        await code_inputs[0].fill(ACCESS_CODE)
        await code_inputs[1].fill(ACCESS_CODE)
    elif len(code_inputs) == 1:
        await code_inputs[0].fill(ACCESS_CODE)
    await page.wait_for_timeout(500)

    # Click Update
    for label in ["Update", "Submit", "Save"]:
        btn = page.get_by_role("button", name=label, exact=False).first
        try:
            if await btn.is_visible(timeout=2000):
                await btn.click()
                break
        except PlaywrightTimeoutError:
            continue
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2000)

    print(f"  {GREEN}OK{RESET}")
    return company


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    creds = read_account()
    if not creds.get("username"):
        sys.exit("ERROR: username not found in Account.txt")

    print(f"\n{BOLD}ISC Access Code Setter{RESET}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        page = await browser.new_page()

        print("  Logging in...", end="", flush=True)
        await login(page, creds)
        print(f"  {GREEN}OK{RESET}")

        print("  Opening Access Codes page...", end="", flush=True)
        await go_to_access_codes(page)
        print(f"  {GREEN}OK{RESET}")

        done = 0
        while True:
            company = await process_one(page)
            if company is None:
                break
            done += 1

        print(f"\n  {BOLD}Done. {done} companies processed.{RESET}")
        await page.wait_for_timeout(3000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
