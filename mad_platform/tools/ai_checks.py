"""AI-assisted Analyst checks -- catches what static rule checks can't.

Per REQUIREMENTS.md section 5.4 step 2: visual review of the screenshot
(is alt text actually descriptive, are focus indicators visible, is there
a contrast issue static analysis missed) and semantic review of
accessible-name content.

No RAG grounding yet -- that's Step 3 (REQUIREMENTS.md section 9), which
upgrades these citations from the model's parametric knowledge to
retrieval-grounded ones. Flagged here as a known, disclosed gap, not a
silent one.

Analyst is deliberately high recall (Guiding Principle 4): the prompts
below explicitly instruct erring toward flagging borderline cases --
Editor is the precision filter, not this layer.
"""

from __future__ import annotations

from pydantic import BaseModel

from mad_platform.tools.crawler import PageSnapshot
from mad_platform.tools.gemini_client import FLASH_LITE, generate_structured


class AIFinding(BaseModel):
    wcag_criterion: str
    description: str
    selector: str
    confidence: float  # 0.0-1.0, Analyst's own rough estimate -- not final


class _AIFindingsResponse(BaseModel):
    findings: list[AIFinding]


_VISUAL_PROMPT = """You are an accessibility analyst reviewing a screenshot of a
rendered webpage for WCAG issues that automated static analysis cannot
catch from HTML alone. Look specifically for:
- Alt text or link text that is technically present but not actually
  descriptive of what it represents (e.g. "image1.jpg", "click here").
- Missing or unclear visual focus indicators on interactive elements.
- Text that appears to have insufficient contrast against its background,
  especially text overlaid on images or gradients.
- Confusing visual reading order or layout that would disorient a screen
  reader or keyboard user.

You are deliberately tuned toward high recall: if something looks
borderline or uncertain, flag it rather than staying silent -- a human
reviewer verifies every flag afterward, so a false alarm costs far less
than a missed violation.

For each issue found, give your best-effort WCAG 2.x success criterion
number (e.g. "1.1.1") based on your own knowledge -- these citations will
be independently verified later, so a reasonable guess is fine.

Page title: {title}
"""

_SEMANTIC_PROMPT = """You are an accessibility analyst reviewing a webpage's
accessible-name content (alt text, aria-labels, link text) for whether it
is genuinely descriptive, not just present. You are NOT checking whether
these attributes exist -- that's already handled by a separate deterministic
check. You are judging QUALITY: does the text actually communicate what a
sighted user would perceive?

Flag cases like:
- alt text that just repeats the filename or says "image"/"photo" without
  describing content.
- Link text like "click here" or "read more" with no context about the
  destination.
- aria-label text that's generic or redundant with visible text nearby.

Deliberately high recall: flag borderline cases, a human reviewer verifies
every flag afterward.

For each issue, give your best-effort WCAG 2.x success criterion number
based on your own knowledge.

Relevant HTML excerpt:
{html_excerpt}
"""


def run_visual_check(snapshot: PageSnapshot) -> list[AIFinding]:
    prompt = _VISUAL_PROMPT.format(title=snapshot.title)
    result = generate_structured(
        FLASH_LITE, prompt, _AIFindingsResponse, image_bytes=snapshot.screenshot_png
    )
    return result.findings


def run_semantic_check(snapshot: PageSnapshot) -> list[AIFinding]:
    # Keep the excerpt bounded -- full page HTML for a large page would be
    # wasteful for a check that only cares about accessible-name content.
    excerpt = snapshot.html[:8000]
    prompt = _SEMANTIC_PROMPT.format(html_excerpt=excerpt)
    result = generate_structured(FLASH_LITE, prompt, _AIFindingsResponse)
    return result.findings
