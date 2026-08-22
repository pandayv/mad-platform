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


# Severity -> (accent color, tint background), standard red/orange/amber/blue
# risk-coding so the reader's eye sorts by severity before reading a word.
_SEVERITY_STYLE = {
    "critical": ("#B91C1C", "#FEF2F2"),
    "high": ("#C2410C", "#FFF7ED"),
    "medium": ("#A16207", "#FFFBEB"),
    "low": ("#1D4ED8", "#EFF6FF"),
}
_DEFAULT_SEVERITY_STYLE = ("#374151", "#F3F4F6")


def _esc(text: str) -> str:
    # Findings text comes from an LLM and has, in practice, contained literal
    # HTML snippets (e.g. a suggested fix quoting `<img alt="...">`) -- escape
    # everything interpolated into the template or it renders as markup
    # instead of visible text, or worse, breaks the page structure.
    return html_lib.escape(str(text))


def _stat_badge(count: int, label: str, color: str) -> str:
    return (
        f'<div class="stat" style="border-top:3px solid {color}">'
        f'<div class="n" style="color:{color}">{count}</div>'
        f'<div class="l">{_esc(label)}</div></div>'
    )


def _finding_card(index: int, r: RankedFinding, ticket: str | None, escalation_id: str | None = None) -> str:
    accent, tint = _SEVERITY_STYLE.get(r.severity.lower(), _DEFAULT_SEVERITY_STYLE)
    if ticket:
        ticket_html = f'<span class="badge" style="background:#DCFCE7;color:#15803D">Filed: {_esc(ticket)}</span>'
    elif escalation_id:
        # Not resolved yet as far as the report knows at generation time --
        # the small script at the end of this page checks the live status
        # on load and updates this badge in place, so a report reopened
        # later reflects what actually happened instead of freezing here.
        ticket_html = (
            f'<span class="badge escalation-badge" data-escalation-id="{_esc(escalation_id)}" '
            f'style="background:#FEF9C3;color:#854D0E">Awaiting internal review</span>'
        )
    else:
        ticket_html = (
            '<span class="badge" style="background:#FEF9C3;color:#854D0E">Awaiting internal review</span>'
        )
    return f"""
    <div class="finding" style="border-left-color:{accent}">
      <div class="finding-header">
        <h3>{index + 1}. WCAG {_esc(r.wcag_criterion)}</h3>
        <span class="badge" style="background:{tint};color:{accent}">{_esc(r.severity)}</span>
      </div>
      <div class="field"><span class="k">Page</span> {_esc(r.page_url)}</div>
      <div class="field"><span class="k">Risk score</span> {r.risk_score:.0f}/100</div>
      <div class="field"><span class="k">Why it matters</span> {_esc(r.risk_rationale)}</div>
      <div class="field"><span class="k">Evidence</span> {_esc(r.editor_rationale)}</div>
      <div class="field"><span class="k">Suggested fix</span></div>
      <div class="fix-box">{_esc(r.suggested_fix)}</div>
      <div class="field" style="margin-top:10px">{ticket_html}</div>
    </div>"""


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Accessibility Report — {title_url}</title>
<style>
  :root {{
    --bg: #f7f8fa; --surface: #ffffff; --text: #1b1e24;
    --text-muted: #5b6472; --border: #dde1e8; --brand: #5b54c9;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  .page {{ max-width: 820px; margin: 0 auto; padding: 40px 24px 56px; }}
  header {{
    border-bottom: 3px solid var(--brand); padding-bottom: 20px; margin-bottom: 28px;
    display: flex; justify-content: space-between; align-items: center; gap: 20px;
  }}
  .brand {{ font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--brand); font-weight: 700; }}
  h1 {{ font-size: 26px; margin: 6px 0 4px; word-break: break-word; }}
  .meta {{ color: var(--text-muted); font-size: 14px; }}
  .score-badge {{
    flex-shrink: 0; width: 84px; height: 84px; border-radius: 50%;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    border: 4px solid; font-weight: 700;
  }}
  .score-badge .n {{ font-size: 26px; line-height: 1; }}
  .score-badge .l {{ font-size: 9px; text-transform: uppercase; letter-spacing: 0.03em; opacity: 0.8; }}
  .summary-box {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 20px 24px; margin-bottom: 20px;
  }}
  .summary-box h2, .findings h2 {{
    margin-top: 0; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted);
  }}
  .summary-box p {{ font-size: 15px; margin-bottom: 0; }}
  .stat-bar {{ display: flex; gap: 12px; margin: 0 0 28px; flex-wrap: wrap; }}
  .stat {{
    flex: 1; background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 18px; text-align: center; min-width: 90px;
  }}
  .stat .n {{ font-size: 26px; font-weight: 700; line-height: 1.2; }}
  .stat .l {{ font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }}
  .finding {{
    background: var(--surface); border: 1px solid var(--border); border-left-width: 5px;
    border-radius: 8px; padding: 18px 22px; margin-bottom: 14px;
  }}
  .finding-header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 12px; }}
  .badge {{
    display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 11px;
    font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; white-space: nowrap;
  }}
  .finding h3 {{ margin: 0; font-size: 16px; }}
  .field {{ margin: 8px 0; font-size: 14px; }}
  .field .k {{ color: var(--text-muted); font-weight: 600; display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 2px; }}
  .fix-box {{
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 10px 14px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 13px; white-space: pre-wrap; word-break: break-word;
  }}
  .empty {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 24px; text-align: center; color: var(--text-muted); }}
  footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border); font-size: 12px; color: var(--text-muted); }}
</style>
</head>
<body>
<div class="page">
  <header>
    <div>
      <div class="brand">MAD Platform — Accessibility Report</div>
      <h1>{title_url}</h1>
      <div class="meta">Generated {generated_at}</div>
    </div>
    <div class="score-badge" style="border-color:{score_color};color:{score_color}">
      <div class="n">{score}</div>
      <div class="l">Score</div>
    </div>
  </header>

  <div class="summary-box">
    <h2>Summary</h2>
    <p>{exec_summary}</p>
  </div>

  <div class="stat-bar">
    {stat_badges}
  </div>

  <div class="findings">
    <h2>Findings ({count})</h2>
    {finding_cards}
  </div>

  <footer>
    MAD Platform — autonomous, AI-assisted WCAG accessibility scanning with independent
    verification before anything is reported. Findings are sorted by real-world risk,
    not raw technical severity alone.
  </footer>
</div>
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
        if (data.ticket_id) {{
          el.textContent = "Filed: " + data.ticket_id;
          el.style.background = "#DCFCE7";
          el.style.color = "#15803D";
        }} else {{
          el.textContent = "Reviewed — dismissed";
          el.style.background = "#F3F4F6";
          el.style.color = "#374151";
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

    stat_badges = _stat_badge(len(ranked), "Total findings", "#374151") + "".join(
        _stat_badge(counts.get(sev, 0), sev.capitalize(), _SEVERITY_STYLE[sev][0]) for sev in ("critical", "high", "medium", "low")
    )

    if not ranked:
        finding_cards = '<div class="empty">No confirmed findings on the pages checked.</div>'
    else:
        finding_cards = "".join(
            _finding_card(i, r, ticket_by_finding.get(i), escalation_by_finding.get(i))
            for i, r in enumerate(ranked)
        )

    return _HTML_TEMPLATE.format(
        title_url=_esc(url),
        generated_at=_esc(generated_at),
        exec_summary=_esc(exec_summary),
        score=score,
        score_color=score_color(score),
        stat_badges=stat_badges,
        count=len(ranked),
        finding_cards=finding_cards,
        app_base_url=_APP_BASE_URL,
    )
