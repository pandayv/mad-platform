"""Crawler tool: fetches a page and captures its rendered HTML + a screenshot.

Deterministic tool, not an LLM agent — Analyst calls this directly. No
judgment happens here.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass

from playwright.async_api import async_playwright


class UnsafeTargetError(Exception):
    """Raised when a URL resolves to a private/link-local/metadata address.

    SSRF guard — the crawler accepts an arbitrary user-supplied URL, so it
    must refuse to fetch internal infrastructure regardless of what the
    caller intended.
    """


class FetchError(Exception):
    """Raised when a page could not be fetched after retries."""


@dataclass
class PageSnapshot:
    url: str
    html: str
    screenshot_png: bytes
    title: str
    text_style_samples: list[dict]


# Walks visible, text-bearing elements and records computed color/background
# — contrast checking needs actual rendered styles, not raw HTML/CSS, since
# color can come from a stylesheet, inline style, or an inherited ancestor.
# Resolves the effective background by walking up through parents past any
# transparent layers, defaulting to white (the common web default) if none
# is found, since CSS itself has no concept of "the final background".
_STYLE_SNAPSHOT_JS = """
() => {
  function getEffectiveBackground(el) {
    let node = el;
    while (node) {
      const bg = getComputedStyle(node).backgroundColor;
      if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
        return bg;
      }
      node = node.parentElement;
    }
    return 'rgb(255, 255, 255)';
  }
  const results = [];
  const elements = document.body.querySelectorAll('*');
  for (const el of elements) {
    const directText = Array.from(el.childNodes)
      .filter(n => n.nodeType === Node.TEXT_NODE)
      .map(n => n.textContent.trim())
      .join(' ')
      .trim();
    if (!directText) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    results.push({
      tag: el.tagName.toLowerCase(),
      text: directText.slice(0, 80),
      color: style.color,
      backgroundColor: getEffectiveBackground(el),
      fontSizePx: parseFloat(style.fontSize),
      fontWeight: style.fontWeight,
    });
  }
  return results;
}
"""


def _assert_safe_target(url: str) -> None:
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname
    if not hostname:
        raise UnsafeTargetError(f"Could not parse a hostname from {url!r}")

    try:
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeTargetError(f"Could not resolve {hostname!r}: {exc}") from exc

    for family, _, _, _, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or str(ip) == "169.254.169.254"  # cloud metadata endpoint, explicit belt-and-suspenders
        ):
            raise UnsafeTargetError(
                f"{hostname!r} resolves to {ip}, which is a private/link-local/"
                f"metadata address — refusing to fetch it."
            )


async def fetch_page(url: str, timeout_ms: int = 15000, retries: int = 2) -> PageSnapshot:
    """Render a page with a real browser and capture its HTML + a full-page screenshot.

    Retries transient failures (timeout, navigation errors) with a short
    backoff — a single flaky load must not fail the whole page, let alone
    the whole cycle.
    """
    _assert_safe_target(url)

    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                try:
                    page = await browser.new_page()
                    await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                    html = await page.content()
                    title = await page.title()
                    screenshot = await page.screenshot(full_page=True)
                    style_samples = await page.evaluate(_STYLE_SNAPSHOT_JS)
                    return PageSnapshot(
                        url=url,
                        html=html,
                        screenshot_png=screenshot,
                        title=title,
                        text_style_samples=style_samples,
                    )
                finally:
                    await browser.close()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, we retry any transient failure
            last_error = exc
            if attempt <= retries:
                await asyncio.sleep(1.5 * attempt)
                continue

    raise FetchError(f"Failed to fetch {url!r} after {retries + 1} attempts: {last_error}") from last_error


def fetch_page_sync(url: str, timeout_ms: int = 15000, retries: int = 2) -> PageSnapshot:
    return asyncio.run(fetch_page(url, timeout_ms, retries))
