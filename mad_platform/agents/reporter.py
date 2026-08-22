"""Reporter: ranks confirmed findings by real-world risk, recommends fixes,
and drafts the report.

rank_and_recommend ranks by WCAG conformance level, real-world litigation
pattern frequency, and estimated user impact -- not raw technical severity
alone. The LLM assigns a risk score per finding; sorting by that score is
deterministic Python, not another judgment call -- code handles the
mechanical part, the model handles the actual judgment.

draft_report renders one fixed template, not a freshly generated structure
per run. The only genuinely LLM-appropriate part of the report itself is
the short executive summary; everything else is templated data fill.

Uses the higher-capability model tier -- ranking/synthesis is a judgment
call worth spending that on, unlike the high-volume per-page checks.
"""

from __future__ import annotations

import html as html_lib
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel

from mad_platform.agents.editor import VerifiedFinding
from mad_platform.tools.adk_client import generate_structured
from mad_platform.tools.gemini_client import FLASH, FLASH_LITE
from mad_platform.web import theme

# The report can be opened outside the app's own origin (downloaded, saved
# locally, reopened later) so the live-status check below needs an
# absolute URL, not a relative fetch that only works when served from
# the app itself.
_APP_BASE_URL = os.environ.get(
    "MAD_APP_BASE_URL", "https://scan-onboarding-803013053073.us-central1.run.app"
)


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


_SCORE_PENALTY = {"critical": 25, "high": 15, "medium": 8, "low": 3}


def compute_score(ranked: list[RankedFinding]) -> int:
    """A single 0-100 "site health" number for the UI's headline display --
    not a WCAG-official metric, just 100 minus a severity-weighted penalty
    per confirmed finding, clamped at 0. Deterministic Python over the
    LLM's already-assigned severities, not another judgment call.
    """
    penalty = sum(_SCORE_PENALTY.get(r.severity.lower(), 5) for r in ranked)
    return max(0, 100 - penalty)


def score_color(score: int) -> str:
    """Shared between the report template and the web UI so the same score
    always reads as the same color in both places."""
    if score >= 80:
        return "#15803D"  # green
    if score >= 50:
        return "#A16207"  # amber
    return "#B91C1C"  # red


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


async def rank_and_recommend(confirmed_by_page: dict[str, list[VerifiedFinding]]) -> list[RankedFinding]:
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
    result = await generate_structured(FLASH, prompt, _RecommendationResponse)

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


# ---------------------------------------------------------------------------
# Step 3: the report artifact itself -- one fixed template, per section 5.5.
# ---------------------------------------------------------------------------

class _ExecutiveSummary(BaseModel):
    summary: str  # 2-3 plain-English sentences, for a non-technical reader


_EXEC_SUMMARY_PROMPT = """Write a 2-3 sentence executive summary of this
accessibility scan for a non-technical small business owner. Plain
English, no jargon, no WCAG citation numbers. Mention the overall risk
level and the single most important thing to act on first.

Site scanned: {url}
Findings, highest risk first (severity, WCAG topic, one-line description):
{summary_lines}
"""


async def generate_executive_summary(url: str, ranked: list[RankedFinding]) -> str:
    if not ranked:
        return (
            "This scan didn't find any confirmed accessibility violations on the "
            "pages checked. That's a good sign, not a guarantee -- only a subset "
            "of WCAG criteria and pages were covered."
        )
    lines = "\n".join(f"- [{r.severity.upper()}] {r.wcag_criterion}: {r.editor_rationale[:100]}" for r in ranked)
    prompt = _EXEC_SUMMARY_PROMPT.format(url=url, summary_lines=lines)
    result = await generate_structured(FLASH_LITE, prompt, _ExecutiveSummary)
    return result.summary


def _esc(text: str) -> str:
    # Findings text comes from an LLM and has, in practice, contained literal
    # HTML snippets (e.g. a suggested fix quoting `<img alt="...">`) -- escape
    # everything interpolated into the template or it renders as markup
    # instead of visible text, or worse, breaks the page structure.
    return html_lib.escape(str(text))


def _status_badge(ticket: str | None, escalation_id: str | None) -> str:
    if ticket:
        return f'<span class="badge sev-ok">Filed: {_esc(ticket)}</span>'
    if escalation_id:
        # Not resolved yet as far as the report knows at generation time --
        # the small script at the end of this page checks the live status
        # on load and updates this badge in place, so a report reopened
        # later reflects what actually happened instead of freezing here.
        return (
            f'<span class="badge sev-pending escalation-badge" '
            f'data-escalation-id="{_esc(escalation_id)}">Awaiting internal review</span>'
        )
    return '<span class="badge sev-pending">Awaiting internal review</span>'


def _finding_row(index: int, r: RankedFinding, ticket: str | None, escalation_id: str | None = None) -> str:
    sev = r.severity.lower()
    rail_color = theme.SEVERITY_VAR.get(sev, "var(--muted)")
    return f"""<tr>
  <td class="rail"><span style="background:{rail_color}"></span></td>
  <td class="num mono">{index + 1}</td>
  <td>
    <div class="finding-title">WCAG {_esc(r.wcag_criterion)} <span class="badge sev-{sev}" style="margin-left:6px">{_esc(r.severity)}</span></div>
    <div class="finding-detail">{_esc(r.risk_rationale)}</div>
  </td>
  <td>{_esc(r.page_url)}</td>
  <td class="num mono">{r.risk_score:.0f}</td>
  <td class="fix-cell">{_esc(r.suggested_fix)}</td>
  <td>{_status_badge(ticket, escalation_id)}</td>
</tr>"""


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Accessibility Report — {title_url}</title>
{font_link}
<style>{theme_css}
.page {{ max-width: 900px; padding: 40px 24px 0; }}
header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; margin-bottom: 24px; }}
header .meta {{ color: var(--muted); font-size: 13.5px; margin-top: 4px; }}
</style>
</head>
<body>
<div class="page">
  <header>
    <div>
      <div class="brand"><span class="dot-b"></span>MAD Platform — Accessibility Report</div>
      <h1>{title_url}</h1>
      <div class="meta">Generated {generated_at}</div>
    </div>
    {score_dial}
  </header>

  {dashboard_row}

  <div class="summary-box">
    <div class="lbl">Executive summary</div>
    <p>{exec_summary}</p>
  </div>

  {findings_section}

</div>
<footer class="note">
  MAD Platform — autonomous, AI-assisted WCAG accessibility scanning with independent
  verification before anything is reported. Findings are sorted by real-world risk,
  not raw technical severity alone.
</footer>
<script>
// Findings under internal review show "Awaiting internal review" as of
// when this report was generated. If this page is reopened later, this
// checks whether each one has since been resolved and updates the badge
// in place -- so a stored report doesn't freeze in a stale state forever.
(function () {{
  document.querySelectorAll(".escalation-badge").forEach(function (el) {{
    var id = el.getAttribute("data-escalation-id");
    fetch("{app_base_url}/api/escalation/" + encodeURIComponent(id) + "/status")
      .then(function (r) {{ return r.ok ? r.json() : null; }})
      .then(function (data) {{
        if (!data || !data.resolved) return;
        el.classList.remove("escalation-badge", "sev-pending");
        if (data.ticket_id) {{
          el.textContent = "Filed: " + data.ticket_id;
          el.classList.add("sev-ok");
        }} else {{
          el.textContent = "Reviewed — dismissed";
        }}
      }})
      .catch(function () {{}});
  }});
}})();
</script>
</body>
</html>
"""


async def draft_report(
    url: str,
    ranked: list[RankedFinding],
    ticket_by_finding: dict[int, str | None] | None = None,
    escalation_by_finding: dict[int, str] | None = None,
) -> str:
    """The fixed report template -- same structure every run, only the data
    changes. Single format (HTML): easiest to generate reliably, opens
    anywhere, and is the one genuinely user-friendly format a business
    owner would actually read. The template itself is fixed; only the
    executive summary is LLM-generated.
    """
    ticket_by_finding = ticket_by_finding or {}
    escalation_by_finding = escalation_by_finding or {}
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    exec_summary = await generate_executive_summary(url, ranked)
    score = compute_score(ranked)

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in ranked:
        counts[r.severity.lower()] = counts.get(r.severity.lower(), 0) + 1
    p_counts = theme.principle_counts([r.wcag_criterion for r in ranked])

    if not ranked:
        findings_section = '<div class="empty">No confirmed findings on the pages checked.</div>'
    else:
        rows = "".join(
            _finding_row(i, r, ticket_by_finding.get(i), escalation_by_finding.get(i))
            for i, r in enumerate(ranked)
        )
        findings_section = f"""<table class="findings-table">
  <thead><tr><th></th><th class="num">#</th><th>Finding</th><th>Page</th><th class="num">Risk</th><th>Suggested fix</th><th>Status</th></tr></thead>
  <tbody>{rows}</tbody>
</table>"""

    return _HTML_TEMPLATE.format(
        title_url=_esc(url),
        generated_at=_esc(generated_at),
        exec_summary=_esc(exec_summary),
        font_link=theme.FONT_LINK,
        theme_css=theme.THEME_CSS,
        score_dial=theme.score_dial(score, score_color(score)),
        dashboard_row=theme.dashboard_row(score, score_color(score), counts, p_counts),
        findings_section=findings_section,
        app_base_url=_APP_BASE_URL,
    )
