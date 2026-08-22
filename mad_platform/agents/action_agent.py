"""Action Agent: files tickets, idempotently, and routes escalations.

- Escalation is a single gate applied once: low Editor confidence OR high
  severity (either alone is sufficient) sends a finding to the SME queue.
- Non-escalated findings are fully autonomous -- ticket filed immediately,
  no human step. Escalated findings wait: no ticket exists until SME
  confirms it ("dismiss" never becomes a ticket, never appears in the
  report) -- this is not act-then-flag for the escalated subset, only for
  the majority that clears the gate.
- Idempotency: a deterministic key per finding means a retried call never
  double-files.

Tool, not an agent -- the judgment already happened upstream (Editor's
confidence, Reporter's severity), this is deterministic routing and API
calls, no LLM judgment of its own.
"""

from __future__ import annotations

import hashlib

from mad_platform.agents.reporter import RankedFinding
from mad_platform.state import firestore_client as fs
from mad_platform.tools.issue_sink import IssueSink

LOW_CONFIDENCE_THRESHOLD = 0.6


def idempotency_key(page_url: str, finding: RankedFinding) -> str:
    raw = f"{page_url}|{finding.wcag_criterion}|{finding.editor_rationale[:100]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def needs_escalation(finding: RankedFinding) -> bool:
    return finding.editor_confidence < LOW_CONFIDENCE_THRESHOLD or finding.severity == "critical"


def _ticket_title(finding: RankedFinding) -> str:
    return f"[{finding.severity.upper()}] WCAG {finding.wcag_criterion} — {finding.page_url}"


def _ticket_description(finding: RankedFinding) -> str:
    return (
        f"WCAG citation: {finding.wcag_criterion}\n"
        f"Severity: {finding.severity} (risk score {finding.risk_score:.0f}/100)\n"
        f"Page: {finding.page_url}\n\n"
        f"Evidence: {finding.editor_rationale}\n\n"
        f"Why it matters: {finding.risk_rationale}\n\n"
        f"Suggested fix: {finding.suggested_fix}"
    )


def route_and_file(sink: IssueSink, ranked: list[RankedFinding]) -> dict[str, list]:
    """The single escalation gate + idempotent filing for the autonomous
    majority. Returns {"filed": [(index, finding, ticket_id)], "escalated":
    [(index, finding, escalation_id)], "already_filed": [(index, finding, ticket_id)]}
    -- index is the finding's position in `ranked`, included explicitly
    rather than left for callers to re-derive via value lookup (fragile if
    two findings ever have identical field values, e.g. near-duplicate
    findings from the same page).
    """
    result: dict[str, list] = {"filed": [], "escalated": [], "already_filed": []}

    for index, finding in enumerate(ranked):
        key = idempotency_key(finding.page_url, finding)
        existing_ticket = fs.get_ticket_for_finding(key)
        if existing_ticket:
            result["already_filed"].append((index, finding, existing_ticket))
            continue

        if needs_escalation(finding):
            fs.create_escalation(
                key,
                {
                    "kind": "finding",
                    "page_url": finding.page_url,
                    "wcag_criterion": finding.wcag_criterion,
                    "severity": finding.severity,
                    "risk_score": finding.risk_score,
                    "editor_rationale": finding.editor_rationale,
                    "editor_confidence": finding.editor_confidence,
                    "risk_rationale": finding.risk_rationale,
                    "suggested_fix": finding.suggested_fix,
                },
            )
            result["escalated"].append((index, finding, key))
            continue

        ticket_id = sink.create_issue(_ticket_title(finding), _ticket_description(finding))
        fs.record_ticket_for_finding(key, ticket_id)
        result["filed"].append((index, finding, ticket_id))

    return result


def resolve_escalation(sink: IssueSink, escalation_id: str, disposition: str, reviewer: str = "sme") -> str | None:
    """SME disposition on a pending escalation. confirm -> files the ticket
    now (idempotency-checked the same as the autonomous path); dismiss ->
    no ticket, ever, for this finding. Returns the ticket ID if one was
    filed, else None.
    """
    if disposition not in ("confirm", "dismiss"):
        raise ValueError(f"disposition must be 'confirm' or 'dismiss', got {disposition!r}")

    data = fs.resolve_escalation(escalation_id, disposition=disposition, reviewer=reviewer)

    if disposition == "dismiss":
        return None

    existing_ticket = fs.get_ticket_for_finding(escalation_id)
    if existing_ticket:
        return existing_ticket

    title = f"[{data['severity'].upper()}] WCAG {data['wcag_criterion']} — {data['page_url']}"
    description = (
        f"WCAG citation: {data['wcag_criterion']}\n"
        f"Severity: {data['severity']} (risk score {data['risk_score']:.0f}/100)\n"
        f"Page: {data['page_url']}\n\n"
        f"Evidence: {data['editor_rationale']}\n\n"
        f"Why it matters: {data['risk_rationale']}\n\n"
        f"Suggested fix: {data['suggested_fix']}\n\n"
        f"[Confirmed by SME review: {reviewer}]"
    )
    ticket_id = sink.create_issue(title, description)
    fs.record_ticket_for_finding(escalation_id, ticket_id)
    return ticket_id
