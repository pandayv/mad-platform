"""A curated subset of WCAG 2.2 success criteria -- the highest-value,
most legally-relevant ones, not exhaustive coverage of all ~90.

Per PLAN.md's own MVP guidance: "don't try to cover the full WCAG spec --
pick the highest-value, most legally-relevant subset." This set covers
what Analyst's rule/AI checks actually target, plus a handful of other
criteria commonly cited in real accessibility litigation, so Editor and
Reporter have real grounding material to retrieve against rather than a
token one-criterion demo.

Descriptions here are accurate factual summaries of the standard, not
verbatim W3C text -- this is a working knowledge base, not a copy of the
spec document.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WCAGCriterion:
    number: str
    title: str
    level: str  # "A", "AA", or "AAA"
    description: str


WCAG_CORPUS: list[WCAGCriterion] = [
    WCAGCriterion(
        "1.1.1", "Non-text Content", "A",
        "All non-text content (images, icons, buttons) that is presented to the "
        "user has a text alternative that serves the equivalent purpose, except "
        "for decorative content, which should have empty alt text or be excluded "
        "from the accessibility tree entirely.",
    ),
    WCAGCriterion(
        "1.3.1", "Info and Relationships", "A",
        "Information, structure, and relationships conveyed through presentation "
        "(such as heading hierarchy, list structure, or table headers) can be "
        "programmatically determined or are available in text -- not conveyed by "
        "visual styling alone.",
    ),
    WCAGCriterion(
        "1.4.3", "Contrast (Minimum)", "AA",
        "Text and images of text have a contrast ratio of at least 4.5:1 against "
        "their background, except large text (18pt, or 14pt bold) which needs "
        "only 3:1, and incidental or logo text which is exempt.",
    ),
    WCAGCriterion(
        "1.4.11", "Non-text Contrast", "AA",
        "Visual information required to identify user interface components "
        "(button borders, form field boundaries, focus indicators) and graphical "
        "objects has a contrast ratio of at least 3:1 against adjacent colors.",
    ),
    WCAGCriterion(
        "1.4.4", "Resize Text", "AA",
        "Text can be resized up to 200% without assistive technology and without "
        "loss of content or functionality.",
    ),
    WCAGCriterion(
        "1.4.10", "Reflow", "AA",
        "Content can be presented without loss of information or functionality, "
        "and without requiring scrolling in two dimensions, at a 320px-equivalent "
        "viewport width.",
    ),
    WCAGCriterion(
        "2.1.1", "Keyboard", "A",
        "All functionality is operable through a keyboard interface, without "
        "requiring specific timings for individual keystrokes, unless the "
        "underlying function requires input that depends on the path of the "
        "user's movement (e.g. free-hand drawing).",
    ),
    WCAGCriterion(
        "2.1.2", "No Keyboard Trap", "A",
        "If keyboard focus can be moved to a component using the keyboard, focus "
        "can also be moved away from it using only the keyboard, without requiring "
        "more than standard exit methods.",
    ),
    WCAGCriterion(
        "2.4.1", "Bypass Blocks", "A",
        "A mechanism is available to bypass blocks of content that repeat across "
        "multiple pages (e.g. a skip-to-main-content link).",
    ),
    WCAGCriterion(
        "2.4.2", "Page Titled", "A",
        "Web pages have titles that describe topic or purpose.",
    ),
    WCAGCriterion(
        "2.4.3", "Focus Order", "A",
        "If a page can be navigated sequentially, components receive focus in an "
        "order that preserves meaning and operability -- natural document order, "
        "not overridden by positive tabindex values that jump around unpredictably.",
    ),
    WCAGCriterion(
        "2.4.4", "Link Purpose (In Context)", "A",
        "The purpose of each link can be determined from the link text alone, or "
        "from the link text together with its programmatically-determined context "
        "(the same sentence, paragraph, list item, or table cell) -- not from "
        "surrounding text that isn't programmatically associated with it.",
    ),
    WCAGCriterion(
        "2.4.6", "Headings and Labels", "AA",
        "Headings and labels describe the topic or purpose of the content or "
        "control they introduce.",
    ),
    WCAGCriterion(
        "2.4.7", "Focus Visible", "AA",
        "Any keyboard-operable interface has a visible indicator when it has "
        "keyboard focus.",
    ),
    WCAGCriterion(
        "3.1.1", "Language of Page", "A",
        "The default human language of each page can be programmatically "
        "determined (via the html lang attribute).",
    ),
    WCAGCriterion(
        "3.3.2", "Labels or Instructions", "A",
        "Labels or instructions are provided when content requires user input, "
        "so the user understands what information is expected in each field.",
    ),
    WCAGCriterion(
        "4.1.2", "Name, Role, Value", "A",
        "For all user interface components, the name and role can be "
        "programmatically determined, states/properties/values that can be set "
        "by the user can be programmatically set, and notification of changes is "
        "available to assistive technology -- ARIA attributes must be used "
        "correctly, not just present, and must reference real, existing elements.",
    ),
    WCAGCriterion(
        "4.1.3", "Status Messages", "AA",
        "Status messages (e.g. a form submission confirmation or an error) can be "
        "programmatically determined through role or properties so assistive "
        "technology can announce them without requiring focus to move to the message.",
    ),
]
