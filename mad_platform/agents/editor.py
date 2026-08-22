"""Editor: independently verifies every finding Analyst produced.

Dismisses false positives with a required written reason, assigns a
validated confidence rating to what survives. Uses the higher-capability
model tier -- this is exactly the kind of low-volume, high-consequence
call worth spending that on.

Grounded against retrieved WCAG text, not just the model's own parametric
knowledge. Retrieval provides candidates, not a forced answer -- semantic
similarity isn't guaranteed to surface the single best match (e.g.
"aria-hidden on a focusable button" can retrieve focus-themed criteria
over the actually-correct Name/Role/Value one), so Editor reasons over
what's retrieved rather than blindly adopting it.

The written rationale on every dismissal is required even though nothing
consumes it yet -- it's the seed of a future feedback loop that could tune
Analyst's flagging behavior from accumulated dismissal patterns, so the
data shape needs to be right from the start even before that loop exists.
"""

from __future__ import annotations

from pydantic import BaseModel

from mad_platform.agents.analyst import RawFinding
from mad_platform.tools.crawler import PageSnapshot
from mad_platform.tools.gemini_client import FLASH, generate_structured
from mad_platform.tools.rag import retrieve as rag_retrieve


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

For each finding, retrieved WCAG reference candidates are provided below
it -- these are the CLOSEST semantic matches found, not a guaranteed
correct answer. Use them to ground your citation when they genuinely fit;
if none of the candidates match the actual issue, use your own knowledge
instead rather than forcing a bad fit.

Findings to verify (index: source, check, Analyst's WCAG guess, description, selector, Analyst's own confidence, retrieved reference candidates):
{findings_list}

Page title: {title}
HTML excerpt:
{html_excerpt}
"""


def _format_findings(findings: list[RawFinding]) -> str:
    lines = []
    for i, f in enumerate(findings):
        candidates = rag_retrieve(f.description, top_k=3)
        candidates_text = "; ".join(f"{c.number} {c.title} ({c.level})" for c in candidates) or "none found"
        lines.append(
            f"{i}: [{f.source}/{f.check}] Analyst guessed WCAG {f.wcag_criterion} -- {f.description} "
            f"(selector: {f.selector}, Analyst confidence: {f.analyst_confidence:.2f})\n"
            f"    retrieved candidates: {candidates_text}"
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
