"""Fetches the current WCAG version from the canonical W3C source.

Deterministic tool, not an LLM call. Parses the version out of the W3C
WAI overview page's link to the current standard (e.g.
href="https://www.w3.org/TR/WCAG22/") rather than free-text prose -- that
URL pattern is W3C's own stable convention for versioned recommendations,
far less likely to drift than page copy.
"""

from __future__ import annotations

import re

import requests

_OVERVIEW_URL = "https://www.w3.org/WAI/standards-guidelines/wcag/"
_VERSION_LINK_RE = re.compile(r"/TR/WCAG(\d)(\d)/")


class WCAGVersionFetchError(Exception):
    """Raised when the current version can't be determined from the page."""


def fetch_current_wcag_version() -> str:
    """Returns the highest WCAG version number found (e.g. "2.2")."""
    response = requests.get(_OVERVIEW_URL, timeout=15)
    response.raise_for_status()

    matches = _VERSION_LINK_RE.findall(response.text)
    if not matches:
        raise WCAGVersionFetchError(
            f"Could not find a WCAG version link (pattern /TR/WCAG\\d\\d/) on {_OVERVIEW_URL}"
        )

    versions = [f"{major}.{minor}" for major, minor in matches]
    return max(versions, key=lambda v: tuple(int(part) for part in v.split(".")))
