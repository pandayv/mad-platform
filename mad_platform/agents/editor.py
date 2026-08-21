"""Editor: independently verifies every finding Analyst produced.

Per REQUIREMENTS.md section 5.4 step 3: dismisses false positives with a
required written reason, assigns a validated confidence rating to what
survives. Uses gemini-3.7-flash (the judgment-tier model, section 6.3) --
this is exactly the kind of low-volume, high-consequence call that tier
is reserved for.

Not yet grounded against retrieved WCAG text (that's Step 3's RAG
addition, REQUIREMENTS.md section 9) -- Editor verifies against the page
evidence (HTML, screenshot) only for now. Flagged, not hidden.

The written rationale on every dismissal is required even though nothing
consumes it yet -- REQUIREMENTS.md section 5.8 loop 2: it's the seed of a
future feedback loop that tunes Analyst, so the data shape has to be
right from day one even before the loop itself is built.
"""

from __future__ import annotations

from pydantic import BaseModel

from mad_platform.agents.analyst import RawFinding
from mad_platform.tools.crawler import PageSnapshot
from mad_platform.tools.gemini_client import FLASH, generate_structured


class VerifiedFinding(BaseModel):
    finding_index: int  # which raw finding this corresponds to, by list position
    confirmed: bool
    wcag_criterion: str  # Editor may correct Analyst's citation
    rationale: str  # required either way -- why confirmed, or why dismissed
    confidence: float  # 0.0-1.0, Editor's own validated rating; only meaningful if confirmed


class _VerificationResponse(BaseModel):
    verifications: list[VerifiedFinding]


_EDITOR_PROMPT = """You are an accessibility Editor. Analyst has flagged the
findings below on a webpage. Your job is to independently verify EACH one
against the actual page evidence (the HTML excerpt and the screenshot),
not to trust Analyst's flag at face value.

Analyst is deliberately tuned toward high recall -- it over-flags on
purpose, so a meaningful fraction of these will be false positives you
should dismiss. A missed real violation is the actual risk; a correctly
dismissed false positive is Analyst and Editor working as designed, not a
failure.

For EVERY finding, whether you confirm or dismiss it, give a specific
rationale grounded in the actual evidence -- not a generic restatement of
the finding. If you dismiss one, say concretely why it doesn't hold up
(e.g. "the img has role=presentation, missing alt is correct here" or
"this text's actual rendered color has sufficient contrast, the flagged
value appears to be a hover state not visible by default").

If you confirm a finding, also give your own confidence rating (0.0-1.0)
reflecting how certain you are this is a real, actionable violation.

Findings to verify (index: source, check, WCAG citation, description, selector, Analyst's own confidence):
{findings_list}

Page title: {title}
HTML excerpt:
{html_excerpt}
"""


def _format_findings(findings: list[RawFinding]) -> str:
    lines = []
    for i, f in enumerate(findings):
        lines.append(
            f"{i}: [{f.source}/{f.check}] WCAG {f.wcag_criterion} -- {f.description} "
            f"(selector: {f.selector}, Analyst confidence: {f.analyst_confidence:.2f})"
        )
    return "\n".join(lines)


def verify_findings(snapshot: PageSnapshot, findings: list[RawFinding]) -> list[VerifiedFinding]:
    if not findings:
        return []

    prompt = _EDITOR_PROMPT.format(
        findings_list=_format_findings(findings),
        title=snapshot.title,
        html_excerpt=snapshot.html[:8000],
    )
    result = generate_structured(
        FLASH, prompt, _VerificationResponse, image_bytes=snapshot.screenshot_png
    )
    return result.verifications
