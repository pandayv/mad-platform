"""ScanJob checkpointing in Firestore.

Each stage writes its completion to the job record as it finishes. On
restart, a caller reads the last completed checkpoint per page and
resumes from the next incomplete stage -- it never blindly re-runs a job
from scratch.

Database is 'scan-firestore', not '(default)' -- an easy-to-miss gotcha:
forgetting the database= argument silently connects to a database that
doesn't have any of this project's data.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-d7e6174e-cca7-4d16-9d5")
_DATABASE = "scan-firestore"

_client = firestore.Client(project=_PROJECT, database=_DATABASE)
_JOBS = _client.collection("scan_jobs")
_TICKETS = _client.collection("filed_tickets")  # idempotency_key -> ticket_id
_ESCALATIONS = _client.collection("escalations")  # SME review queue
_KB_VERSION = _client.collection("knowledge_base_version").document("wcag")

# Stages, in order -- used to answer "what's the next incomplete stage".
PAGE_STAGES = ["crawled", "analyzed", "verified"]


def create_job(url: str, trigger_type: str = "one-time") -> str:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    _JOBS.document(job_id).set(
        {
            "url": url,
            "trigger_type": trigger_type,
            "status": "in_progress",
            "pages": {},
            "created_at": now,
            "updated_at": now,
        }
    )
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    doc = _JOBS.document(job_id).get()
    return doc.to_dict() if doc.exists else None


def get_page_stage(job_id: str, page_url: str) -> str | None:
    """Returns the last completed stage for a page, or None if not started
    -- this is what a resuming caller checks before deciding to redo work.
    """
    job = get_job(job_id)
    if not job:
        return None
    return job.get("pages", {}).get(page_url, {}).get("stage")


def checkpoint_page_crawled(job_id: str, page_url: str) -> None:
    _set_page_field(job_id, page_url, {"stage": "crawled"})


def checkpoint_page_analyzed(job_id: str, page_url: str, raw_finding_count: int) -> None:
    _set_page_field(job_id, page_url, {"stage": "analyzed", "raw_finding_count": raw_finding_count})


def checkpoint_page_retry(job_id: str, page_url: str, reason: str) -> None:
    """Records that the bounded retry gate sent this page back for one
    more pass, and why. No "stage" field here on purpose -- this doesn't
    move the page forward, it just makes the decision auditable.
    """
    _set_page_field(job_id, page_url, {"retried": True, "retry_reason": reason})


def checkpoint_page_verified(job_id: str, page_url: str, verified_findings: list[dict], retried: bool = False) -> None:
    _set_page_field(job_id, page_url, {"stage": "verified", "findings": verified_findings, "retried": retried})


def set_job_phase(job_id: str, phase: str) -> None:
    """A coarser signal than the per-page stage checkpoints, for the gaps
    between them where nothing else gets written -- page selection (after
    the entry crawl, before any page-level checkpoint exists) and the
    ranking/filing/report tail (after every page hits "verified" but before
    complete_job). Without this, a status page watching only per-page
    stages goes silent during both, and a healthy multi-second wait reads
    identically to a hang.
    """
    _JOBS.document(job_id).update({"phase": phase, "updated_at": datetime.now(timezone.utc)})


def complete_job(job_id: str) -> None:
    _JOBS.document(job_id).update({"status": "completed", "updated_at": datetime.now(timezone.utc)})


def save_scan_summary(job_id: str, summary: dict[str, Any]) -> None:
    """Persists the final scan outcome (score, severity counts, report
    location, ticket/escalation counts) onto the job record -- the web UI's
    status endpoint reads this back rather than needing the in-process
    ScanResult, since the request that started the scan and the request
    that polls for its result are two different HTTP calls.
    """
    _JOBS.document(job_id).update({"summary": summary, "updated_at": datetime.now(timezone.utc)})


def fail_job(job_id: str, error: str) -> None:
    _JOBS.document(job_id).update(
        {"status": "failed", "error": error, "updated_at": datetime.now(timezone.utc)}
    )


def get_ticket_for_finding(idempotency_key: str) -> str | None:
    """Checks whether a finding has already been filed -- the idempotency
    guard that keeps a retried pipeline step from double-filing.
    """
    doc = _TICKETS.document(idempotency_key).get()
    return doc.to_dict()["ticket_id"] if doc.exists else None


def record_ticket_for_finding(idempotency_key: str, ticket_id: str) -> None:
    _TICKETS.document(idempotency_key).set(
        {"ticket_id": ticket_id, "filed_at": datetime.now(timezone.utc)}
    )


def create_escalation(idempotency_key: str, finding_data: dict) -> str:
    """Adds a finding to the SME queue -- no ticket is filed for it until
    resolved. Escalated findings wait; they don't act-then-flag like the
    non-escalated majority.
    """
    doc_ref = _ESCALATIONS.document(idempotency_key)
    doc_ref.set(
        {
            **finding_data,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
        }
    )
    return idempotency_key


def list_pending_escalations() -> list[dict[str, Any]]:
    return [{"id": doc.id, **doc.to_dict()} for doc in _ESCALATIONS.where(
        filter=firestore.FieldFilter("status", "==", "pending")
    ).stream()]


def resolve_escalation(escalation_id: str, disposition: str, reviewer: str = "sme") -> dict[str, Any]:
    """disposition: 'confirm' or 'dismiss'. Returns the escalation's data
    so the caller (Action Agent) can file a ticket if confirmed.
    """
    doc_ref = _ESCALATIONS.document(escalation_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise ValueError(f"No escalation found with id {escalation_id!r}")
    data = doc.to_dict()
    doc_ref.update(
        {
            "status": "resolved",
            "disposition": disposition,
            "reviewer": reviewer,
            "resolved_at": datetime.now(timezone.utc),
        }
    )
    return data


def get_kb_version() -> dict[str, Any] | None:
    doc = _KB_VERSION.get()
    return doc.to_dict() if doc.exists else None


def touch_kb_check(checked_version: str) -> None:
    """Records that a freshness check just ran, independent of whether the
    version actually changed -- so "last checked" is always accurate even
    on a no-op tick.
    """
    _KB_VERSION.set(
        {"last_checked_version": checked_version, "last_checked_at": datetime.now(timezone.utc)},
        merge=True,
    )


def set_kb_version(version: str) -> None:
    """Called after a successful refresh (auto or SME-confirmed) -- records
    which version the currently-stored embeddings actually reflect.
    """
    _KB_VERSION.set({"version": version, "updated_at": datetime.now(timezone.utc)}, merge=True)


def _set_page_field(job_id: str, page_url: str, fields: dict) -> None:
    """Merges the given fields into a page's record -- but never regresses
    its stage backward. Firestore's merge=True is field-path-recursive, not
    a whole-object replace, so writing {"stage": "crawled"} onto a page
    already at "verified" would otherwise silently downgrade it -- exactly
    the kind of bug that defeats resumability while looking correct at a
    glance, causing full reprocessing on every "resume" instead of none.
    """
    new_stage = fields.get("stage")
    if new_stage in PAGE_STAGES:
        current_stage = get_page_stage(job_id, page_url)
        if current_stage in PAGE_STAGES and PAGE_STAGES.index(current_stage) > PAGE_STAGES.index(new_stage):
            fields = {k: v for k, v in fields.items() if k != "stage"}
            if not fields:
                return

    doc_ref = _JOBS.document(job_id)
    doc_ref.set({"pages": {page_url: fields}, "updated_at": datetime.now(timezone.utc)}, merge=True)
