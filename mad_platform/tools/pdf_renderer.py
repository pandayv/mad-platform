"""Renders a self-contained HTML report to PDF.

Deterministic tool, not an LLM agent -- reuses the Playwright/Chromium
dependency the crawler already needs, rather than adding a new PDF library.
No network fetch happens here: the HTML is set directly via set_content, so
this works offline and doesn't re-trigger the crawler's SSRF guard.
"""

from __future__ import annotations

from playwright.async_api import async_playwright


async def html_to_pdf(html: str) -> bytes:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="load")
            return await page.pdf(format="Letter", print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        finally:
            await browser.close()
