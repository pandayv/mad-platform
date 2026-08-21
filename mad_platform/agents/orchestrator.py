"""Orchestrator: picks pages, sequences the cycle, checkpoints progress.

Per REQUIREMENTS.md section 5.4 step 1 and Guiding Principle 1: page
selection is the dynamic, LLM-driven judgment point -- not exhaustive
crawling, not a fixed list, an actual decision grounded in what's on the
entry page. Everything after that is deterministic sequencing.

Resumability (Guiding Principle 2): a page already checkpointed all the
way to "verified" is skipped entirely on resume, not redone. A page
interrupted partway through is redone from its crawl -- crawling is cheap
and idempotent, so this is a deliberate, disclosed scope decision rather
than persisting large intermediate finding blobs just to save one re-crawl.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel

from mad_platform.agents.action_agent import route_and_file
from mad_platform.agents.analyst import RawFinding, analyze_page
from mad_platform.agents.editor import VerifiedFinding, verify_findings
from mad_platform.agents.reporter import RankedFinding, draft_report, rank_and_recommend
from mad_platform.state import firestore_client as fs
from mad_platform.state import storage_client
from mad_platform.tools.crawler import PageSnapshot, fetch_page
from mad_platform.tools.gemini_client import FLASH, FLASH_LITE, generate_structured
from mad_platform.tools.issue_sink import IssueSink, MockIssueSink

MAX_ADDITIONAL_PAGES = 2


class _PageSelection(BaseModel):
    selected_paths: list[str]  # relative paths chosen from the candidate list
    reasoning: str


_PAGE_SELECTION_PROMPT = """You are coordinating an accessibility scan of a
website. You've loaded the entry page and found these candidate links to
other pages on the same site. Pick up to {max_pages} of them that are most
likely to carry real accessibility and legal risk -- prioritize primary
navigation, contact forms, checkout/cart flows, and account/login pages
over marketing or blog content. Not exhaustive crawling -- a bounded,
justified subset.

Entry page: {entry_url}
Candidate links (path: link text):
{candidates}

Return the paths you selected (not full URLs) and a short reasoning.
"""


def _normalize_path(path: str) -> str:
    """Empty path (a bare "https://example.com" entry URL) and "/" are the
    same page -- treat them identically everywhere paths get compared.
    Caught for real: without this, a nav link back to "/" on the entry
    page got treated as a distinct candidate from the entry URL itself,
    causing the same page to be crawled and analyzed twice under two URL
    forms, duplicating every finding on it in the final report.
    """
    return path or "/"


def _extract_candidate_links(snapshot: PageSnapshot, max_candidates: int = 30) -> dict[str, str]:
    soup = BeautifulSoup(snapshot.html, "html.parser")
    base = urlparse(snapshot.url)
    entry_path = _normalize_path(base.path)
    candidates: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        absolute = urljoin(snapshot.url, href)
        parsed = urlparse(absolute)
        if parsed.netloc != base.netloc:
            continue  # same-domain only
        if parsed.scheme not in ("http", "https"):
            continue
        path = _normalize_path(parsed.path)
        if path == entry_path:
            continue  # just a link back to the entry page itself, not a new candidate
        text = a.get_text(strip=True)[:60]
        candidates.setdefault(path, text)
        if len(candidates) >= max_candidates:
            break
    return candidates


def select_pages(entry_snapshot: PageSnapshot) -> list[str]:
    """Returns absolute URLs: the entry page plus up to MAX_ADDITIONAL_PAGES
    chosen by an LLM call over same-domain links found on it.
    """
    candidates = _extract_candidate_links(entry_snapshot)
    if not candidates:
        return [entry_snapshot.url]

    candidate_lines = "\n".join(f"{path}: {text or '(no link text)'}" for path, text in candidates.items())
    prompt = _PAGE_SELECTION_PROMPT.format(
        max_pages=MAX_ADDITIONAL_PAGES, entry_url=entry_snapshot.url, candidates=candidate_lines
    )
    selection = generate_structured(FLASH_LITE, prompt, _PageSelection)

    base = urlparse(entry_snapshot.url)
    selected_urls = [entry_snapshot.url]
    seen_paths = {_normalize_path(base.path)}
    for path in selection.selected_paths[:MAX_ADDITIONAL_PAGES]:
        if path in candidates and path not in seen_paths:
            selected_urls.append(f"{base.scheme}://{base.netloc}{path}")
            seen_paths.add(path)
    return selected_urls


class _RetryDecision(BaseModel):
    proceed: bool  # True = good enough, move on. False = use the one retry allowance.
    reasoning: str


_RETRY_GATE_PROMPT = """You are the Orchestrator overseeing an accessibility
scan. Editor has just verified Analyst's findings for a page. Decide: is
this analysis good enough to proceed, or does the page warrant one more,
deeper look from Analyst?

Send it back for another pass only if there's a real reason to -- e.g.
confidence on confirmed findings is low across the board, or several
dismissals reflect genuine uncertainty rather than a confident correction,
suggesting the first pass didn't have enough to go on. Do NOT send it back
just because the page had few or zero findings -- a clean, well-built page
is a valid, complete result, not evidence of an insufficient pass.

This decision is capped at one retry maximum regardless of your answer --
you are deciding whether to use that single allowance, not opening a loop.

Editor's verification results for this page:
{summary}
"""


def _format_verification_summary(verified: list[VerifiedFinding]) -> str:
    if not verified:
        return "(no findings at all -- Analyst flagged nothing on this page)"
    lines = []
    for v in verified:
        status = "CONFIRMED" if v.confirmed else "DISMISSED"
        lines.append(f"- [{status}] WCAG {v.wcag_criterion}, confidence {v.confidence:.2f}: {v.rationale}")
    return "\n".join(lines)


def evaluate_retry_gate(verified: list[VerifiedFinding]) -> _RetryDecision:
    prompt = _RETRY_GATE_PROMPT.format(summary=_format_verification_summary(verified))
    return generate_structured(FLASH, prompt, _RetryDecision)


async def _run_analysis_pass(url: str) -> tuple[PageSnapshot, list[RawFinding], list[VerifiedFinding]]:
    snapshot = await fetch_page(url)
    raw_findings = await analyze_page(snapshot)
    verified = verify_findings(snapshot, raw_findings)
    return snapshot, raw_findings, verified


async def _process_page(job_id: str, url: str) -> list[VerifiedFinding]:
    snapshot, raw_findings, verified = await _run_analysis_pass(url)
    fs.checkpoint_page_crawled(job_id, url)
    fs.checkpoint_page_analyzed(job_id, url, raw_finding_count=len(raw_findings))

    decision = evaluate_retry_gate(verified)
    retried = False
    if not decision.proceed:
        fs.checkpoint_page_retry(job_id, url, reason=decision.reasoning)
        snapshot, raw_findings, verified = await _run_analysis_pass(url)  # one more pass, capped -- no loop
        retried = True

    fs.checkpoint_page_verified(
        job_id,
        url,
        verified_findings=[
            {**v.model_dump(), "raw": raw_findings[v.finding_index].__dict__} for v in verified
        ],
        retried=retried,
    )
    return verified


def _findings_from_stored(stored: list[dict]) -> list[VerifiedFinding]:
    """Firestore stores verified findings as plain dicts (plus a "raw"
    field checkpoint_page_verified adds) -- converts back to VerifiedFinding
    so resumed results are the same type as freshly-produced ones. This was
    a real, previously-undetected gap: nothing downstream consumed a
    resumed job's results type-strictly until Reporter/Action Agent were
    wired in below, so the mismatch never surfaced in testing before now.
    """
    return [VerifiedFinding(**{k: v for k, v in d.items() if k != "raw"}) for d in stored]


@dataclass
class ScanResult:
    job_id: str
    findings_by_page: dict[str, list[VerifiedFinding]]
    report: str
    report_uri: str
    filed: list[tuple[int, RankedFinding, str]]
    escalated: list[tuple[int, RankedFinding, str]]
    already_filed: list[tuple[int, RankedFinding, str]]


async def run_one_time_scan(
    url: str, job_id: str | None = None, issue_sink: IssueSink | None = None
) -> ScanResult:
    """The full core, end to end: site -> findings -> recommendations ->
    report -> escalation. Pass an existing job_id to resume it -- pages
    already fully verified are skipped, everything else is (re)run from
    its crawl. issue_sink defaults to a mock (real Jira credentials are a
    separate setup step, SETUP.md item 18, not a code dependency).
    """
    issue_sink = issue_sink or MockIssueSink()
    existing_job = fs.get_job(job_id) if job_id else None

    if existing_job and existing_job.get("pages"):
        # True resume: reuse the page list this job already decided on,
        # rather than re-running page selection (a fresh LLM call isn't
        # guaranteed to pick the same pages twice, and doesn't need to --
        # resuming means continuing the same job, not re-deciding its scope).
        pages = list(existing_job["pages"].keys())
    else:
        if job_id is None:
            job_id = fs.create_job(url)
        entry_snapshot = await fetch_page(url)
        fs.checkpoint_page_crawled(job_id, url)
        pages = select_pages(entry_snapshot)

    results: dict[str, list[VerifiedFinding]] = {}
    try:
        for page_url in pages:
            if fs.get_page_stage(job_id, page_url) == "verified":
                job = fs.get_job(job_id)
                results[page_url] = _findings_from_stored(job["pages"][page_url]["findings"])
                continue
            verified = await _process_page(job_id, page_url)
            results[page_url] = verified

        confirmed_by_page = {
            page_url: [f for f in findings if f.confirmed] for page_url, findings in results.items()
        }
        ranked = rank_and_recommend(confirmed_by_page)
        filing = route_and_file(issue_sink, ranked)

        # Build finding index -> ticket (or None if pending SME review),
        # so the report reflects what actually happened rather than a
        # stale "not filed yet" placeholder.
        ticket_by_finding: dict[int, str | None] = {}
        for index, _finding, ticket_id in filing["filed"] + filing["already_filed"]:
            ticket_by_finding[index] = ticket_id
        for index, _finding, _escalation_id in filing["escalated"]:
            ticket_by_finding[index] = None

        report = draft_report(url, ranked, ticket_by_finding)
        report_uri = storage_client.save_report(job_id, report)
        fs.complete_job(job_id)
    except Exception as exc:  # noqa: BLE001
        fs.fail_job(job_id, str(exc))
        raise

    return ScanResult(
        job_id=job_id,
        findings_by_page=results,
        report=report,
        report_uri=report_uri,
        filed=filing["filed"],
        escalated=filing["escalated"],
        already_filed=filing["already_filed"],
    )
