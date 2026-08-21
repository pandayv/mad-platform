"""Reporter: ranks confirmed findings by real-world risk, recommends fixes.

Step 2 scope (REQUIREMENTS.md section 9): findings -> recommendations.
The full report artifact (executive summary, fixed template) is Step 3 --
this module produces the ranked, fix-annotated data that step consumes,
not the formatted document itself.

Per REQUIREMENTS.md section 5.4 step 5: ranks by WCAG conformance level,
real-world litigation pattern frequency, and estimated user impact -- not
raw technical severity alone. The LLM assigns a risk score per finding;
sorting by that score is deterministic Python, not another judgment call --
consistent with keeping code responsible for the mechanical part and the
model responsible for the actual judgment.

Uses gemini-3.7-flash (the judgment tier, section 6.3) -- Reporter's
ranking/synthesis is explicitly one of the calls that tier is reserved for.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from mad_platform.agents.editor import VerifiedFinding
from mad_platform.tools.gemini_client import FLASH, generate_structured


@dataclass
class RankedFinding:
    page_url: str
    wcag_criterion: str
    editor_rationale: str
    editor_confidence: float
    risk_score: float  # 0-100, Reporter's judgment
    severity: str  # "critical" | "high" | "medium" | "low"
    suggested_fix: str
    risk_rationale: str


class _Recommendation(BaseModel):
    finding_index: int
    risk_score: float
    severity: str
    suggested_fix: str
    risk_rationale: str


class _RecommendationResponse(BaseModel):
    recommendations: list[_Recommendation]


_REPORTER_PROMPT = """You are the Reporter for an accessibility scan. Editor
has confirmed the findings below as real violations. For each one, assess
its real-world risk -- not just technical severity -- and recommend a
concrete fix.

Weigh three things when scoring risk (0-100): the WCAG conformance level
implied by the criterion (Level A violations are generally higher-risk
than AAA), how often this type of issue shows up in real accessibility
litigation (missing alt text, unlabeled form fields, and low contrast on
key interactions are common targets; obscure AAA-only issues rarely are),
and estimated impact on actual users trying to complete a task (a broken
checkout form field is worse than a decorative image on a footer link).

Assign severity as one of: critical, high, medium, low.

Give a concrete suggested fix for each -- not "fix the alt text" but the
actual text/attribute/markup change that would resolve it, inferred from
the finding's description.

Confirmed findings (index: page, WCAG citation, Editor's rationale, confidence):
{findings_list}
"""


def _format_findings(findings: list[tuple[str, VerifiedFinding]]) -> str:
    lines = []
    for i, (page_url, f) in enumerate(findings):
        lines.append(
            f"{i}: [{page_url}] WCAG {f.wcag_criterion} (confidence {f.confidence:.2f}) -- {f.rationale}"
        )
    return "\n".join(lines)


def rank_and_recommend(confirmed_by_page: dict[str, list[VerifiedFinding]]) -> list[RankedFinding]:
    """confirmed_by_page: page URL -> its CONFIRMED VerifiedFinding list
    (dismissed findings don't need a recommendation, so filter before calling).
    Returns findings sorted by risk_score, highest first.
    """
    flat: list[tuple[str, VerifiedFinding]] = [
        (page_url, f) for page_url, findings in confirmed_by_page.items() for f in findings
    ]
    if not flat:
        return []

    prompt = _REPORTER_PROMPT.format(findings_list=_format_findings(flat))
    result = generate_structured(FLASH, prompt, _RecommendationResponse)

    ranked = [
        RankedFinding(
            page_url=flat[rec.finding_index][0],
            wcag_criterion=flat[rec.finding_index][1].wcag_criterion,
            editor_rationale=flat[rec.finding_index][1].rationale,
            editor_confidence=flat[rec.finding_index][1].confidence,
            risk_score=rec.risk_score,
            severity=rec.severity,
            suggested_fix=rec.suggested_fix,
            risk_rationale=rec.risk_rationale,
        )
        for rec in result.recommendations
    ]
    ranked.sort(key=lambda r: r.risk_score, reverse=True)
    return ranked
