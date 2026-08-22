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

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from mad_platform.agents.orchestrator import run_one_time_scan
from mad_platform.agents.reporter import compute_score, score_color
from mad_platform.state import firestore_client as fs
from mad_platform.state import storage_client

logger = logging.getLogger("mad_platform.web")

app = FastAPI(title="MAD Platform")

# scan-onboarding is deployed with --allow-unauthenticated -- a business
# owner has to be able to just hit the URL. That means the app itself is
# the only thing standing between this endpoint and someone using it as a
# free Gemini-calling, Playwright-fetching open relay. If MAD_ACCESS_CODE
# is set (Cloud Run deploys it from Secret Manager), a scan requires it;
# if unset (local dev), the gate is open.
_ACCESS_CODE = os.environ.get("MAD_ACCESS_CODE")

_BASE_STYLE = """
:root {
  --bg: #f7f8fa; --surface: #ffffff; --text: #1b1e24;
  --text-muted: #5b6472; --border: #dde1e8; --brand: #5b54c9;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text); min-height: 100vh;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
.page { max-width: 640px; margin: 0 auto; padding: 60px 24px; }
.brand { font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--brand); font-weight: 700; margin-bottom: 6px; }
h1 { font-size: 26px; margin: 0 0 8px; }
.tagline { color: var(--text-muted); font-size: 15px; margin-bottom: 32px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 28px; }
input[type=url] {
  width: 100%; padding: 12px 14px; font-size: 15px; border: 1px solid var(--border);
  border-radius: 8px; margin-bottom: 14px;
}
button, .btn {
  display: inline-block; background: var(--brand); color: #fff; border: none;
  padding: 12px 20px; font-size: 15px; font-weight: 600; border-radius: 8px;
  cursor: pointer; text-decoration: none;
}
button:hover, .btn:hover { opacity: 0.92; }
.btn-secondary { background: var(--surface); color: var(--brand); border: 1px solid var(--brand); }
.error-box { background: #FEF2F2; border: 1px solid #FCA5A5; color: #991B1B; border-radius: 8px; padding: 14px 18px; margin-top: 16px; }
"""


def _render_form(error: str | None = None) -> str:
    code_field = (
        '<input type="password" name="code" placeholder="Access code" required style="margin-top:10px" autocomplete="off">'
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
<style>{_BASE_STYLE}</style>
</head>
<body>
<div class="page">
  <div class="brand">MAD Platform</div>
  <h1>Scan a website for accessibility risk</h1>
  <div class="tagline">Autonomous WCAG scanning, verified findings, real tickets filed -- not just a report.</div>
  <div class="card">
    <form action="/scan" method="post">
      <input type="url" name="url" placeholder="https://example.com" required autofocus>
      {code_field}
      <div style="margin-top:14px"><button type="submit">Scan now</button></div>
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
<style>__STYLE__
.stage-list { list-style: none; padding: 0; margin: 20px 0 0; }
.stage-list li { padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 14px; display: flex; justify-content: space-between; }
.stage-list li:last-child { border-bottom: none; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 8px; }
.spinner {
  display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border);
  border-top-color: var(--brand); border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }
.score-badge {
  width: 84px; height: 84px; border-radius: 50%; display: flex; flex-direction: column;
  align-items: center; justify-content: center; border: 4px solid; font-weight: 700; flex-shrink: 0;
}
.score-badge .n { font-size: 26px; line-height: 1; }
.score-badge .l { font-size: 9px; text-transform: uppercase; letter-spacing: 0.03em; opacity: 0.8; }
.result-header { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 20px; }
.stat-bar { display: flex; gap: 10px; margin: 16px 0; flex-wrap: wrap; }
.stat { flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; text-align: center; min-width: 80px; }
.stat .n { font-size: 20px; font-weight: 700; }
.stat .l { font-size: 10px; color: var(--text-muted); text-transform: uppercase; }
.actions { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }
</style>
</head>
<body>
<div class="page">
  <div class="brand"><a href="/" style="color:inherit;text-decoration:none">MAD Platform</a></div>
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
const severityColor = {critical: "#B91C1C", high: "#C2410C", medium: "#A16207", low: "#1D4ED8"};

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
  const color = done ? "#15803D" : (stage ? "#A16207" : "#9CA3AF");
  return `<span class="dot" style="background:${color}"></span>`;
}

function renderInProgress(data) {
  if (!startTimeMs && data.created_at) startTimeMs = new Date(data.created_at).getTime();
  document.getElementById("heading").textContent = "Scanning " + data.url;
  const phaseLabel = PHASE_LABELS[data.phase] || "Starting...";
  let rows = Object.entries(data.pages).map(([url, info]) => {
    const stage = info.stage || "pending";
    return `<li><span>${stageDot(info.stage)}${esc(url)}</span><span style="color:var(--text-muted)">${esc(stage)}</span></li>`;
  }).join("");
  document.getElementById("content").innerHTML =
    `<div style="display:flex;justify-content:space-between;align-items:center">` +
    `<span><span class="spinner"></span> ${esc(phaseLabel)}</span>` +
    `<span id="elapsed" style="color:var(--text-muted);font-size:13px">${elapsedText()}</span></div>` +
    (rows ? `<ul class="stage-list">${rows}</ul>` : "");
}

function renderCompleted(data) {
  finished = true;
  const s = data.summary;
  document.getElementById("heading").textContent = "Scan complete";
  document.getElementById("tagline").textContent = data.url;
  // (textContent above is inherently safe -- only the innerHTML build below needs esc())
  const counts = s.severity_counts;
  const statHtml = ["critical", "high", "medium", "low"].map(sev =>
    `<div class="stat" style="border-top:3px solid ${severityColor[sev]}">
       <div class="n" style="color:${severityColor[sev]}">${counts[sev] || 0}</div>
       <div class="l">${sev}</div>
     </div>`
  ).join("");
  document.getElementById("content").innerHTML = `
    <div class="result-header">
      <div>
        <div style="font-size:15px;color:var(--text-muted)">${s.total_findings} confirmed finding(s) &middot; ${s.filed_count} ticket(s) filed &middot; ${s.escalated_count} awaiting SME review</div>
      </div>
      <div class="score-badge" style="border-color:${s.score_color};color:${s.score_color}">
        <div class="n">${s.score}</div>
        <div class="l">Score</div>
      </div>
    </div>
    <div class="stat-bar">${statHtml}</div>
    <div class="actions">
      <a class="btn" href="/report/${jobId}" target="_blank">View full report</a>
      <a class="btn btn-secondary" href="/report/${jobId}?download=1">Download HTML</a>
      <a class="btn btn-secondary" href="/">Scan another site</a>
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
        result = await run_one_time_scan(url, job_id=job_id)
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
    return _STATUS_PAGE.replace("__STYLE__", _BASE_STYLE).replace(
        "__URL__", html.escape(job["url"])
    ).replace("__JOB_ID__", f'"{job_id}"')


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
