"""Platform UI: a minimal web front end over the scan pipeline.

A submission form plus a polling status page, running on the
scan-onboarding Cloud Run service. On-demand, manual scans only --
recurring/event-driven triggers (GitHub webhook, Scheduler) are a
separate, not-yet-built layer.

A scan takes 30-90s -- too long for one synchronous request -- so
POST /scan fires the pipeline as a background asyncio task and redirects
immediately to a status page that polls Firestore (which the pipeline
already checkpoints to) every couple of seconds. No new agent logic, no
new datastore -- this is a thin view over what the pipeline already writes.

Run locally: .venv/bin/uvicorn mad_platform.web.app:app --reload --port 8080
"""

from __future__ import annotations

import asyncio
import html
import logging
import os

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from mad_platform.agents.action_agent import resolve_escalation as resolve_finding_escalation
from mad_platform.agents.orchestrator import run_one_time_scan
from mad_platform.agents.pattern_miner import resolve_pattern_escalation
from mad_platform.agents.reporter import compute_score, score_color
from mad_platform.agents.wcag_auto_heal import resolve_kb_escalation
from mad_platform.state import firestore_client as fs
from mad_platform.state import storage_client
from mad_platform.tools.issue_sink import IssueSink, JiraIssueSink, MockIssueSink
from mad_platform.web import theme

logger = logging.getLogger("mad_platform.web")

app = FastAPI(title="MAD Platform")


def _issue_sink() -> IssueSink:
    """Real Jira when credentials are configured (Cloud Run deploys them
    from Secret Manager); MockIssueSink otherwise, so local dev and tests
    still run without live Jira credentials.
    """
    if os.environ.get("JIRA_URL"):
        return JiraIssueSink()
    return MockIssueSink()

# scan-onboarding is deployed with --allow-unauthenticated -- a business
# owner has to be able to just hit the URL. That means the app itself is
# the only thing standing between this endpoint and someone using it as a
# free Gemini-calling, Playwright-fetching open relay. If MAD_ACCESS_CODE
# is set (Cloud Run deploys it from Secret Manager), a scan requires it;
# if unset (local dev), the gate is open.
_ACCESS_CODE = os.environ.get("MAD_ACCESS_CODE")

# The SME review queue is a separate trust boundary from the public scan
# form -- REQUIREMENTS §5.6 is explicit that it "must not be exposed to
# the customer/business owner". Deliberately a different code from
# MAD_ACCESS_CODE, not the same one reused, so having one doesn't imply
# having the other. The session cookie just holds the code itself rather
# than an issued token -- a reasonable simplification for this scope, not
# a production-grade session mechanism.
_REVIEW_CODE = os.environ.get("MAD_REVIEW_CODE")
_REVIEW_COOKIE = "mad_review_session"


def _is_reviewer(request: Request) -> bool:
    return not _REVIEW_CODE or request.cookies.get(_REVIEW_COOKIE) == _REVIEW_CODE

_BASE_STYLE = theme.THEME_CSS


def _render_form(error: str | None = None) -> str:
    code_field = (
        '<label class="f-label" for="code">Access code</label>'
        '<input id="code" type="password" name="code" required autocomplete="off">'
        if _ACCESS_CODE
        else ""
    )
    error_html = f'<div class="error-box">{html.escape(error)}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MAD Platform — Accessibility Scan</title>
{theme.FONT_LINK}
<style>{_BASE_STYLE}</style>
</head>
<body>
<div class="page">
  <div class="brand"><span class="dot-b"></span>MAD Platform</div>
  <h1>Scan a website for accessibility risk</h1>
  <div class="tagline">Autonomous WCAG scanning, verified findings, real tickets filed -- not just a report.</div>
  <div class="card">
    <form action="/scan" method="post">
      <label class="f-label" for="url">Website URL</label>
      <input id="url" type="url" name="url" placeholder="https://example.com" required autofocus>
      {code_field}
      <div style="margin-top:14px"><button type="submit">Scan now →</button></div>
    </form>
    {error_html}
  </div>
</div>
</body>
</html>
"""


_STATUS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scanning — MAD Platform</title>
__FONT_LINK__
<style>__STYLE__</style>
</head>
<body>
<div class="page">
  <div class="brand"><a href="/" style="color:inherit;text-decoration:none"><span class="dot-b"></span>MAD Platform</a></div>
  <h1 id="heading">Scanning __URL__</h1>
  <div class="tagline" id="tagline">This runs the real pipeline: page selection, parallel analysis, independent verification, ranking, ticket filing.</div>
  <div class="error-box" id="slow-warning" style="display:none;margin-bottom:16px">
    This is taking longer than usual (3+ minutes). Most scans finish in under 90s -- the
    site may be unusually heavy, or something may need attention. Feel free to keep
    waiting, or come back and check this page later.
  </div>
  <div class="card" id="content">
    <span class="spinner"></span> Starting...
  </div>
</div>
<script>
const jobId = __JOB_ID__;
const SEV_ORDER = ["critical", "high", "medium", "low"];
const SEV_VAR = {critical: "var(--crit)", high: "var(--high)", medium: "var(--med)", low: "var(--low)"};
const PRINCIPLE_ORDER = ["Perceivable", "Operable", "Understandable", "Robust"];

function donutSvg(counts) {
  const total = SEV_ORDER.reduce((s, k) => s + (counts[k] || 0), 0);
  const r = 40, C = 2 * Math.PI * r;
  let circles = `<circle cx="48" cy="48" r="${r}" fill="none" stroke="var(--border)" stroke-width="14"/>`;
  let offset = 0;
  for (const sev of SEV_ORDER) {
    const count = counts[sev] || 0;
    if (!count) continue;
    const len = (count / total) * C;
    circles += `<circle cx="48" cy="48" r="${r}" fill="none" stroke="${SEV_VAR[sev]}" stroke-width="14" ` +
      `stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"/>`;
    offset += len;
  }
  const legend = SEV_ORDER.map(sev =>
    `<li><span class="lg-dot" style="background:${SEV_VAR[sev]}"></span>${sev[0].toUpperCase()}${sev.slice(1)}<b>${counts[sev] || 0}</b></li>`
  ).join("");
  return `<div class="donut-wrap"><svg width="88" height="88" viewBox="0 0 96 96" role="img" aria-label="${total} findings by severity">` +
    `<g transform="rotate(-90 48 48)">${circles}</g>` +
    `<text x="48" y="45" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="19" font-weight="800" fill="var(--ink)">${total}</text>` +
    `<text x="48" y="59" text-anchor="middle" font-family="Public Sans, sans-serif" font-size="7.5" fill="var(--muted)" letter-spacing="0.4">FINDINGS</text></svg>` +
    `<ul class="donut-legend">${legend}</ul></div>`;
}

function catChart(counts) {
  const max = Math.max(1, ...PRINCIPLE_ORDER.map(p => counts[p] || 0));
  const rows = PRINCIPLE_ORDER.map(p => {
    const n = counts[p] || 0;
    return `<div class="cat-row"><span class="cat-lbl">${p}</span><div class="cat-bar-track">` +
      `<div class="cat-bar-fill" style="width:${(n / max * 100).toFixed(0)}%"></div></div><span class="cat-n">${n}</span></div>`;
  }).join("");
  return `<div class="cat-chart">${rows}</div>`;
}

function scoreNote(counts) {
  const c = counts.critical || 0, h = counts.high || 0;
  if (c) return `${c} critical issue${c !== 1 ? "s" : ""} ${c === 1 ? "needs" : "need"} immediate attention.`;
  if (h) return `${h} high-severity issue${h !== 1 ? "s" : ""} found, nothing critical.`;
  const total = SEV_ORDER.reduce((s, k) => s + (counts[k] || 0), 0);
  return total ? "Only medium- and low-severity issues found." : "No confirmed findings on the pages checked.";
}

// Human-readable label per orchestrator phase (mad_platform/agents/
// orchestrator.py's fs.set_job_phase calls) -- without this, the gaps
// between per-page checkpoints (page selection, then ranking/filing/
// report generation at the end) show nothing at all, and a healthy
// multi-second wait looks identical to a hang.
const PHASE_LABELS = {
  crawling_entry_page: "Loading the site...",
  selecting_pages: "Deciding which pages matter most...",
  analyzing_pages: "Analyzing pages for accessibility issues...",
  ranking_findings: "Ranking findings by real-world risk...",
  filing_tickets: "Filing tickets for confirmed findings...",
  generating_report: "Generating your report...",
};

let startTimeMs = null;
let finished = false;

function elapsedText() {
  if (!startTimeMs) return "";
  const secs = Math.max(0, Math.floor((Date.now() - startTimeMs) / 1000));
  return secs < 60 ? `${secs}s elapsed` : `${Math.floor(secs / 60)}m ${secs % 60}s elapsed`;
}

function tickElapsed() {
  if (finished) return;
  const el = document.getElementById("elapsed");
  if (el) el.textContent = elapsedText();
  const warn = document.getElementById("slow-warning");
  if (warn && startTimeMs && (Date.now() - startTimeMs) / 1000 > 180) {
    warn.style.display = "block";
  }
}
setInterval(tickElapsed, 1000);

// The scanned URL, page URLs, and any error message all trace back to
// user-supplied input (the form's url field) -- escape before innerHTML.
function esc(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function stageDot(stage) {
  const done = stage === "verified";
  const color = done ? "var(--ok)" : (stage ? "var(--med)" : "var(--border)");
  return `<span class="stg-dot" style="background:${color}"></span>`;
}

function renderInProgress(data) {
  if (!startTimeMs && data.created_at) startTimeMs = new Date(data.created_at).getTime();
  document.getElementById("heading").textContent = "Scanning " + data.url;
  const phaseLabel = PHASE_LABELS[data.phase] || "Starting...";
  let rows = Object.entries(data.pages).map(([url, info]) => {
    const stage = info.stage || "pending";
    return `<li><span>${stageDot(info.stage)}${esc(url)}</span><span style="color:var(--muted)">${esc(stage)}</span></li>`;
  }).join("");
  document.getElementById("content").innerHTML =
    `<div style="display:flex;justify-content:space-between;align-items:center">` +
    `<span><span class="spinner"></span> ${esc(phaseLabel)}</span>` +
    `<span id="elapsed" style="color:var(--muted);font-size:13px">${elapsedText()}</span></div>` +
    (rows ? `<ul class="stage-list">${rows}</ul>` : "");
}

function renderCompleted(data) {
  finished = true;
  const s = data.summary;
  document.getElementById("heading").textContent = "Scan complete";
  document.getElementById("tagline").textContent = data.url;
  // (textContent above is inherently safe -- only the innerHTML build below needs esc())
  const counts = s.severity_counts;
  const pCounts = s.principle_counts || {};
  document.getElementById("content").innerHTML = `
    <div class="meta-line">${s.total_findings} confirmed finding(s) &middot; ${s.filed_count} ticket(s) filed &middot; ${s.escalated_count} awaiting SME review</div>
    <div class="dash-row">
      <div class="dash-card"><div class="dc-title">Site score</div>
        <div class="dash-score">
          <div class="score-dial" style="border-color:${s.score_color};color:${s.score_color}"><div class="n">${s.score}</div><div class="l">Score</div></div>
          <div class="score-note">${esc(scoreNote(counts))}</div>
        </div>
      </div>
      <div class="dash-card"><div class="dc-title">By severity</div>${donutSvg(counts)}</div>
      <div class="dash-card"><div class="dc-title">By WCAG principle</div>${catChart(pCounts)}</div>
    </div>
    <div class="actions">
      <a class="btn" href="/report/${jobId}" target="_blank">View full report</a>
      <a class="btn ghost" href="/report/${jobId}?download=1">Download HTML</a>
      <a class="btn ghost" href="/">Scan another site</a>
    </div>`;
}

function renderFailed(data) {
  finished = true;
  document.getElementById("heading").textContent = "Scan failed";
  document.getElementById("content").innerHTML =
    `<div class="error-box">${esc(data.error || "Unknown error")}</div>
     <div class="actions"><a class="btn" href="/">Try again</a></div>`;
}

async function poll() {
  const res = await fetch(`/api/status/${jobId}`);
  if (!res.ok) return;
  const data = await res.json();
  if (data.status === "completed") { renderCompleted(data); return; }
  if (data.status === "failed") { renderFailed(data); return; }
  renderInProgress(data);
  setTimeout(poll, 2000);
}
poll();
</script>
</body>
</html>
"""


async def _run_and_store(job_id: str, url: str) -> None:
    try:
        result = await run_one_time_scan(url, job_id=job_id, issue_sink=_issue_sink())
    except Exception:
        logger.exception("Scan failed for job %s (%s)", job_id, url)
        return  # run_one_time_scan already wrote status=failed to Firestore before re-raising

    all_ranked = [f for _, f, _ in result.filed + result.escalated + result.already_filed]
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in all_ranked:
        counts[r.severity.lower()] = counts.get(r.severity.lower(), 0) + 1
    score = compute_score(all_ranked)

    fs.save_scan_summary(
        job_id,
        {
            "score": score,
            "score_color": score_color(score),
            "severity_counts": counts,
            "principle_counts": theme.principle_counts([r.wcag_criterion for r in all_ranked]),
            "total_findings": len(all_ranked),
            "filed_count": len(result.filed) + len(result.already_filed),
            "escalated_count": len(result.escalated),
            "report_uri": result.report_uri,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def form_page() -> str:
    return _render_form()


@app.post("/scan")
async def start_scan(url: str = Form(...), code: str = Form("")) -> Response:
    if _ACCESS_CODE and code != _ACCESS_CODE:
        return HTMLResponse(_render_form(error="Wrong access code."), status_code=403)
    job_id = fs.create_job(url)
    asyncio.create_task(_run_and_store(job_id, url))
    return RedirectResponse(f"/status/{job_id}", status_code=303)


@app.get("/status/{job_id}", response_class=HTMLResponse)
async def status_page(job_id: str) -> str:
    job = fs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "No such job")
    return (
        _STATUS_PAGE.replace("__STYLE__", _BASE_STYLE)
        .replace("__FONT_LINK__", theme.FONT_LINK)
        .replace("__URL__", html.escape(job["url"]))
        .replace("__JOB_ID__", f'"{job_id}"')
    )


@app.get("/api/status/{job_id}")
async def api_status(job_id: str) -> JSONResponse:
    job = fs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "No such job")
    created_at = job.get("created_at")
    return JSONResponse(
        {
            "job_id": job_id,
            "url": job["url"],
            "status": job.get("status", "in_progress"),
            "phase": job.get("phase"),
            "error": job.get("error"),
            "pages": {url: {"stage": info.get("stage")} for url, info in job.get("pages", {}).items()},
            "summary": job.get("summary"),
            "created_at": created_at.isoformat() if created_at else None,
        }
    )


@app.get("/report/{job_id}")
async def get_report(job_id: str, download: int = 0) -> Response:
    content = storage_client.read_report(job_id)
    if content is None:
        raise HTTPException(404, "Report not found (job may not be complete yet)")
    headers = {"Content-Disposition": f'attachment; filename="{job_id}.html"'} if download else {}
    return Response(content=content, media_type="text/html", headers=headers)


@app.get("/api/escalation/{escalation_id}/status")
async def escalation_status(escalation_id: str) -> JSONResponse:
    """Public, unauthenticated on purpose -- this is what closes the loop
    for a report reopened later (see reporter.py's escalation-badge
    script). Deliberately minimal: whether it's resolved and, if so,
    whether a ticket was filed. No reviewer identity, no rationale, no
    internal deliberation -- nothing here would be a problem for a
    customer to see, unlike the review queue itself.
    """
    escalation = fs.get_escalation(escalation_id)
    if escalation is None:
        raise HTTPException(404, "No such escalation")
    resolved = escalation.get("status") == "resolved"
    ticket_id = fs.get_ticket_for_finding(escalation_id) if resolved else None
    # A saved/downloaded report is opened from whatever origin the viewer's
    # browser gives it (file://, a different host entirely) -- this only
    # works cross-origin with an explicit CORS header, and it's safe to
    # allow any origin here since the payload is already public-safe.
    return JSONResponse(
        {"resolved": resolved, "ticket_id": ticket_id},
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _render_review_login(error: str | None = None) -> str:
    error_html = f'<div class="error-box">{html.escape(error)}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internal Review — MAD Platform</title>
{theme.FONT_LINK}
<style>{_BASE_STYLE}</style>
</head>
<body>
<div class="page">
  <div class="brand"><span class="dot-b"></span>MAD Platform</div>
  <h1>Internal review queue</h1>
  <div class="tagline">Not for customer access — authorized reviewers only.</div>
  <div class="card">
    <form action="/review/login" method="post">
      <label class="f-label" for="rcode">Review code</label>
      <input id="rcode" type="password" name="code" required autofocus autocomplete="off">
      <div style="margin-top:14px"><button type="submit">Enter</button></div>
    </form>
    {error_html}
  </div>
</div>
</body>
</html>"""


def _review_item_row(e: dict) -> str:
    eid = html.escape(e["id"])
    kind = e.get("kind")
    if kind == "kb_version_change":
        return (
            f'<tr><td><span class="badge sev-low">KB version</span></td>'
            f"<td>WCAG {html.escape(str(e.get('old_version')))} → {html.escape(str(e.get('new_version')))} "
            f"— classified {html.escape(str(e.get('change_type')))}</td>"
            f'<td class="mono">{e.get("confidence", 0):.2f}</td>'
            f'<td><a class="btn btn-secondary" href="/review/{eid}" style="padding:6px 14px;font-size:12.5px">Review →</a></td></tr>'
        )
    if kind == "learned_pattern":
        return (
            f'<tr><td><span class="badge sev-low">Learned pattern</span></td>'
            f"<td>WCAG {html.escape(str(e.get('wcag_criterion', '?')))} — seen {e.get('occurrence_count', 0)} time(s)</td>"
            f'<td class="mono">{e.get("confidence", 0):.2f}</td>'
            f'<td><a class="btn btn-secondary" href="/review/{eid}" style="padding:6px 14px;font-size:12.5px">Review →</a></td></tr>'
        )
    sev = str(e.get("severity", "medium")).lower()
    return (
        f'<tr><td><span class="badge sev-{sev}">Finding</span></td>'
        f"<td>WCAG {html.escape(str(e.get('wcag_criterion', '?')))} — {html.escape(str(e.get('page_url', '')))}</td>"
        f'<td class="mono">{e.get("editor_confidence", 0):.2f}</td>'
        f'<td><a class="btn btn-secondary" href="/review/{eid}" style="padding:6px 14px;font-size:12.5px">Review →</a></td></tr>'
    )


def _render_review_list(pending: list[dict]) -> str:
    if not pending:
        items_html = '<div class="tagline" style="margin:0">Nothing pending — the queue is empty.</div>'
    else:
        rows = "".join(_review_item_row(e) for e in pending)
        items_html = (
            '<table class="q-list"><thead><tr><th>Type</th><th>Detail</th><th>Confidence</th><th></th></tr>'
            f"</thead><tbody>{rows}</tbody></table>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internal Review — MAD Platform</title>
{theme.FONT_LINK}
<style>{_BASE_STYLE}</style>
</head>
<body>
<div class="page wide">
  <div class="brand"><span class="dot-b"></span>MAD Platform</div>
  <h1>Internal review queue</h1>
  <div class="tagline">{len(pending)} item(s) awaiting disposition.</div>
  <div class="card">{items_html}</div>
</div>
</body>
</html>"""


def _render_review_detail(e: dict, message: str | None = None) -> str:
    eid = e["id"]
    message_html = f'<div class="success-box">{html.escape(message)}</div>' if message else ""

    if e.get("kind") == "kb_version_change":
        body = f"""
          <div class="field"><b>WCAG version change</b> <span class="badge sev-low">KB version</span></div>
          <div class="field">{html.escape(str(e.get('old_version')))} → {html.escape(str(e.get('new_version')))}</div>
          <div class="field">Classified: {html.escape(str(e.get('change_type')))} (confidence {e.get('confidence', 0):.2f})</div>
          <div class="field">{html.escape(str(e.get('reasoning', '')))}</div>
        """
    elif e.get("kind") == "learned_pattern":
        samples = "".join(
            f'<div class="fix-cell" style="max-width:none;margin-bottom:6px">{html.escape(str(r))}</div>'
            for r in e.get("sample_rationales", [])
        )
        body = f"""
          <div class="field"><b>Learned dismissal pattern</b> <span class="badge sev-low">Learned pattern</span></div>
          <div class="field"><b>WCAG criterion:</b> {html.escape(str(e.get('wcag_criterion', '')))}</div>
          <div class="field"><b>Seen:</b> {e.get('occurrence_count', 0)} time(s) across independent scans</div>
          <div class="field"><b>Confidence:</b> <span class="mono">{e.get('confidence', 0):.2f}</span></div>
          <div class="field"><b>Pattern:</b> {html.escape(str(e.get('pattern_description', '')))}</div>
          <div class="field"><b>Sample dismissal rationales:</b></div>
          {samples}
          <div class="field" style="margin-top:10px;color:var(--muted);font-size:12.5px">
            Confirming adds this to Editor's grounding on every future scan. Dismissing discards it -- the miner won't re-propose this exact pattern.
          </div>
        """
    else:
        sev = str(e.get("severity", "medium")).lower()
        body = f"""
          <div class="field"><b>WCAG {html.escape(str(e.get('wcag_criterion', '')))}</b> <span class="badge sev-{sev}">{html.escape(str(e.get('severity', '')))}</span></div>
          <div class="field"><b>Page:</b> {html.escape(str(e.get('page_url', '')))}</div>
          <div class="field"><b>Confidence:</b> <span class="mono">{e.get('editor_confidence', 0):.2f}</span></div>
          <div class="field"><b>Evidence:</b> {html.escape(str(e.get('editor_rationale', '')))}</div>
          <div class="field"><b>Suggested fix:</b></div>
          <div class="fix-cell" style="max-width:none">{html.escape(str(e.get('suggested_fix', '')))}</div>
        """

    resolved = e.get("status") == "resolved"
    if resolved:
        actions = f'<div class="tagline" style="margin:0">Already resolved: {html.escape(str(e.get("disposition")))}.</div>'
    else:
        actions = f"""
          <form action="/review/{eid}/resolve" method="post" style="display:inline-block;margin-right:10px">
            <button type="submit" name="disposition" value="confirm">Confirm</button>
          </form>
          <form action="/review/{eid}/resolve" method="post" style="display:inline-block">
            <button type="submit" name="disposition" value="dismiss" class="btn-secondary">Dismiss</button>
          </form>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internal Review — MAD Platform</title>
{theme.FONT_LINK}
<style>{_BASE_STYLE}</style>
</head>
<body>
<div class="page">
  <div class="brand"><a href="/review" style="color:inherit;text-decoration:none"><span class="dot-b"></span>MAD Platform — Review Queue</a></div>
  <h1>Review item</h1>
  <div class="card">
    {body}
    <div style="margin-top:20px">{actions}</div>
  </div>
  {message_html}
</div>
</body>
</html>"""


@app.get("/review", response_class=HTMLResponse)
async def review_list(request: Request) -> Response:
    if not _is_reviewer(request):
        return HTMLResponse(_render_review_login())
    return HTMLResponse(_render_review_list(fs.list_pending_escalations()))


@app.post("/review/login")
async def review_login(code: str = Form(...)) -> Response:
    if _REVIEW_CODE and code != _REVIEW_CODE:
        return HTMLResponse(_render_review_login(error="Wrong review code."), status_code=403)
    resp = RedirectResponse("/review", status_code=303)
    resp.set_cookie(_REVIEW_COOKIE, _REVIEW_CODE or "", httponly=True, samesite="lax")
    return resp


@app.get("/review/{escalation_id}", response_class=HTMLResponse)
async def review_detail(escalation_id: str, request: Request) -> Response:
    if not _is_reviewer(request):
        return HTMLResponse(_render_review_login())
    escalation = fs.get_escalation(escalation_id)
    if escalation is None:
        raise HTTPException(404, "No such escalation")
    return HTMLResponse(_render_review_detail(escalation))


@app.post("/review/{escalation_id}/resolve")
async def review_resolve(escalation_id: str, request: Request, disposition: str = Form(...)) -> Response:
    if not _is_reviewer(request):
        return HTMLResponse(_render_review_login())
    escalation = fs.get_escalation(escalation_id)
    if escalation is None:
        raise HTTPException(404, "No such escalation")
    if escalation.get("status") == "resolved":
        return HTMLResponse(_render_review_detail(escalation, message="Already resolved."))

    kind = escalation.get("kind")
    if kind == "kb_version_change":
        resolve_kb_escalation(escalation_id, disposition=disposition, reviewer="web-review")
    elif kind == "learned_pattern":
        resolve_pattern_escalation(escalation_id, disposition=disposition, reviewer="web-review")
    else:
        resolve_finding_escalation(_issue_sink(), escalation_id, disposition=disposition, reviewer="web-review")

    updated = fs.get_escalation(escalation_id)
    return HTMLResponse(_render_review_detail(updated, message=f"Marked {disposition}."))
