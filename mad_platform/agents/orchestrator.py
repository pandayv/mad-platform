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

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel

from mad_platform.agents.analyst import RawFinding, analyze_page
from mad_platform.agents.editor import VerifiedFinding, verify_findings
from mad_platform.state import firestore_client as fs
from mad_platform.tools.crawler import PageSnapshot, fetch_page
from mad_platform.tools.gemini_client import FLASH_LITE, generate_structured

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


def _extract_candidate_links(snapshot: PageSnapshot, max_candidates: int = 30) -> dict[str, str]:
    soup = BeautifulSoup(snapshot.html, "html.parser")
    base = urlparse(snapshot.url)
    candidates: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        absolute = urljoin(snapshot.url, href)
        parsed = urlparse(absolute)
        if parsed.netloc != base.netloc:
            continue  # same-domain only
        if parsed.scheme not in ("http", "https"):
            continue
        text = a.get_text(strip=True)[:60]
        candidates.setdefault(parsed.path or "/", text)
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
    for path in selection.selected_paths[:MAX_ADDITIONAL_PAGES]:
        if path in candidates:
            selected_urls.append(f"{base.scheme}://{base.netloc}{path}")
    return selected_urls


async def _process_page(job_id: str, url: str) -> list[VerifiedFinding]:
    snapshot = await fetch_page(url)
    fs.checkpoint_page_crawled(job_id, url)

    raw_findings: list[RawFinding] = await analyze_page(snapshot)
    fs.checkpoint_page_analyzed(job_id, url, raw_finding_count=len(raw_findings))

    verified = verify_findings(snapshot, raw_findings)
    fs.checkpoint_page_verified(
        job_id,
        url,
        verified_findings=[
            {**v.model_dump(), "raw": raw_findings[v.finding_index].__dict__} for v in verified
        ],
    )
    return verified


async def run_one_time_scan(url: str, job_id: str | None = None) -> dict[str, list[VerifiedFinding]]:
    """Step 1, end to end: site -> verified findings, one URL in.

    Pass an existing job_id to resume it -- pages already fully verified
    are skipped, everything else is (re)run from its crawl.
    """
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
                results[page_url] = job["pages"][page_url]["findings"]
                continue
            verified = await _process_page(job_id, page_url)
            results[page_url] = verified
        fs.complete_job(job_id)
    except Exception as exc:  # noqa: BLE001
        fs.fail_job(job_id, str(exc))
        raise

    return results
