"""Deterministic WCAG rule checks — the non-LLM half of Analyst.

Missing/empty alt text, contrast ratios, heading hierarchy, form label
association, ARIA attribute misuse, tab-order heuristics. These are plain
functions, not agent/tool-call overhead — there's no judgment here, just
code that either finds a violation or doesn't.

Analyst is deliberately tuned high-recall: these checks lean toward
flagging borderline cases rather than staying silent. Editor is what
filters false positives later, not this layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from mad_platform.tools.crawler import PageSnapshot


@dataclass
class RuleFinding:
    check: str
    wcag_criterion: str  # best-effort citation; Editor/RAG validates this later
    message: str
    selector: str  # short human-readable description of the offending element


# ---------------------------------------------------------------------------
# 1. Missing alt text
# ---------------------------------------------------------------------------

def check_alt_text(soup: BeautifulSoup) -> list[RuleFinding]:
    """Flags <img> tags with no alt attribute at all.

    alt="" is a valid, intentional choice for decorative images (WCAG
    explicitly allows it) — only a genuinely MISSING attribute is flagged.
    """
    findings = []
    for img in soup.find_all("img"):
        if img.get("alt") is None:
            findings.append(
                RuleFinding(
                    check="alt_text",
                    wcag_criterion="1.1.1",
                    message="<img> has no alt attribute at all (missing, not empty).",
                    selector=_describe(img),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 2. Heading hierarchy
# ---------------------------------------------------------------------------

def check_heading_hierarchy(soup: BeautifulSoup) -> list[RuleFinding]:
    """Flags skipped heading levels (e.g. h1 straight to h3) and missing h1."""
    findings = []
    headings = soup.find_all(re.compile(r"^h[1-6]$"))
    levels = [int(h.name[1]) for h in headings]

    if not levels:
        return findings

    if levels[0] != 1:
        findings.append(
            RuleFinding(
                check="heading_hierarchy",
                wcag_criterion="1.3.1",
                message=f"First heading on the page is h{levels[0]}, not h1.",
                selector=_describe(headings[0]),
            )
        )

    for prev, cur, tag in zip(levels, levels[1:], headings[1:]):
        if cur > prev + 1:
            findings.append(
                RuleFinding(
                    check="heading_hierarchy",
                    wcag_criterion="1.3.1",
                    message=f"Heading level jumps from h{prev} to h{cur}, skipping a level.",
                    selector=_describe(tag),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 3. Form label association
# ---------------------------------------------------------------------------

def check_form_labels(soup: BeautifulSoup) -> list[RuleFinding]:
    """Flags inputs with no associated label, aria-label, or aria-labelledby."""
    findings = []
    labelled_ids = {label.get("for") for label in soup.find_all("label") if label.get("for")}

    for field in soup.find_all(["input", "select", "textarea"]):
        field_type = field.get("type", "").lower()
        if field_type in ("hidden", "submit", "button", "image"):
            continue

        has_id_label = field.get("id") in labelled_ids
        has_aria_label = bool(field.get("aria-label") or field.get("aria-labelledby"))
        has_wrapping_label = field.find_parent("label") is not None

        if not (has_id_label or has_aria_label or has_wrapping_label):
            findings.append(
                RuleFinding(
                    check="form_label",
                    wcag_criterion="1.3.1",
                    message=f"<{field.name}> has no associated label (no <label for>, "
                            f"aria-label, aria-labelledby, or wrapping <label>).",
                    selector=_describe(field),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 4. ARIA attribute misuse (lightweight, high-value patterns only)
# ---------------------------------------------------------------------------

def check_aria_misuse(soup: BeautifulSoup) -> list[RuleFinding]:
    findings = []

    # aria-hidden="true" on a focusable element makes it invisible to
    # screen readers but still tab-reachable — a well-known trap.
    for el in soup.find_all(attrs={"aria-hidden": "true"}):
        if el.name in ("a", "button", "input", "select", "textarea") or el.get("tabindex") is not None:
            findings.append(
                RuleFinding(
                    check="aria_misuse",
                    wcag_criterion="4.1.2",
                    message="Element is aria-hidden but still focusable "
                            "(interactive tag or explicit tabindex).",
                    selector=_describe(el),
                )
            )

    # aria-labelledby referencing an id that doesn't exist on the page.
    all_ids = {el.get("id") for el in soup.find_all(id=True)}
    for el in soup.find_all(attrs={"aria-labelledby": True}):
        for ref_id in el["aria-labelledby"].split():
            if ref_id not in all_ids:
                findings.append(
                    RuleFinding(
                        check="aria_misuse",
                        wcag_criterion="4.1.2",
                        message=f"aria-labelledby references id {ref_id!r}, which doesn't exist on the page.",
                        selector=_describe(el),
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# 5. Tab-order heuristics
# ---------------------------------------------------------------------------

def check_tab_order(soup: BeautifulSoup) -> list[RuleFinding]:
    """Flags positive tabindex values — a well-known anti-pattern that
    overrides natural document order and usually breaks keyboard navigation.
    """
    findings = []
    for el in soup.find_all(attrs={"tabindex": True}):
        try:
            value = int(el["tabindex"])
        except ValueError:
            continue
        if value > 0:
            findings.append(
                RuleFinding(
                    check="tab_order",
                    wcag_criterion="2.4.3",
                    message=f"tabindex={value} is positive, which overrides natural "
                            f"tab order — a common source of confusing keyboard navigation.",
                    selector=_describe(el),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 6. Color contrast
# ---------------------------------------------------------------------------

_RGB_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _relative_luminance(r: int, g: int, b: int) -> float:
    def channel(c: int) -> float:
        c_srgb = c / 255.0
        return c_srgb / 12.92 if c_srgb <= 0.03928 else ((c_srgb + 0.055) / 1.055) ** 2.4

    r_lin, g_lin, b_lin = channel(r), channel(g), channel(b)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def _contrast_ratio(fg: str, bg: str) -> float | None:
    fg_match, bg_match = _RGB_RE.search(fg), _RGB_RE.search(bg)
    if not fg_match or not bg_match:
        return None
    l_fg = _relative_luminance(*(int(x) for x in fg_match.groups()))
    l_bg = _relative_luminance(*(int(x) for x in bg_match.groups()))
    lighter, darker = max(l_fg, l_bg), min(l_fg, l_bg)
    return (lighter + 0.05) / (darker + 0.05)


def check_contrast(snapshot: PageSnapshot) -> list[RuleFinding]:
    """Flags text with a computed contrast ratio below the WCAG AA threshold.

    Large text (>=18px, or >=14px bold) needs 3:1; everything else needs
    4.5:1. Requires the crawler's computed-style samples, not raw HTML —
    contrast can't be determined from markup alone.
    """
    findings = []
    for sample in snapshot.text_style_samples:
        ratio = _contrast_ratio(sample["color"], sample["backgroundColor"])
        if ratio is None:
            continue

        is_large = sample["fontSizePx"] >= 18 or (
            sample["fontSizePx"] >= 14 and sample["fontWeight"] in ("bold", "700", "800", "900")
        )
        threshold = 3.0 if is_large else 4.5

        if ratio < threshold:
            findings.append(
                RuleFinding(
                    check="contrast",
                    wcag_criterion="1.4.3",
                    message=f"Text {sample['text']!r} has a contrast ratio of {ratio:.2f}:1, "
                            f"below the {threshold:.1f}:1 threshold "
                            f"({'large' if is_large else 'normal'} text).",
                    selector=f"<{sample['tag']}> {sample['text'][:40]!r}",
                )
            )
    return findings


# ---------------------------------------------------------------------------

def _describe(el: Tag) -> str:
    attrs = "".join(f'[{k}="{v}"]' for k, v in el.attrs.items() if k in ("id", "class", "name", "type"))
    return f"<{el.name}{attrs}>"


def run_all_rule_checks(snapshot: PageSnapshot) -> list[RuleFinding]:
    """Runs every deterministic check against one page snapshot."""
    soup = BeautifulSoup(snapshot.html, "html.parser")
    findings: list[RuleFinding] = []
    findings += check_alt_text(soup)
    findings += check_heading_hierarchy(soup)
    findings += check_form_labels(soup)
    findings += check_aria_misuse(soup)
    findings += check_tab_order(soup)
    findings += check_contrast(snapshot)
    return findings
