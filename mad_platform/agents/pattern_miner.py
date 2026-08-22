"""Pattern miner: finds recurring, consistent dismissal patterns in
Editor's own history and proposes them as calibration notes for future
scans -- the persistent-memory loop for the Analyst/Editor pipeline
itself, distinct from wcag_auto_heal's persistent memory for the shared
knowledge base.

Runs on Editor's dismissals specifically (confirmed=False, every scan),
not the rarer SME escalation queue -- Editor already independently
verifies every finding, so its own dismissal history is the high-volume,
already-real signal for "Analyst keeps making the same mistake here."

Deliberately local (see gemma_client.py): this is a periodic batch job
with no live-request latency pressure, not a per-scan call.

A mined pattern never applies itself. It's proposed as a SME-review
escalation (kind="learned_pattern", reusing the same review queue as
findings and KB version changes) and only becomes persistent memory
(firestore_client.save_learned_pattern) once a human confirms it --
Analyst's whole design is high recall on purpose ("a missed real
violation is the actual risk"), so anything that could suppress future
detection needs a person to sign off, the same way a major WCAG version
change does before the knowledge base auto-updates.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from pydantic import BaseModel

from mad_platform.state import firestore_client as fs
from mad_platform.tools import gemma_client

MIN_OCCURRENCES = 3  # fewer than this isn't a pattern, just one editor call
SAMPLE_SIZE = 6  # rationales shown to Gemma per cluster -- enough to judge consistency, not the whole history
CONFIDENCE_THRESHOLD = 0.75


class _PatternAssessment(BaseModel):
    is_consistent_pattern: bool
    pattern_description: str
    confidence: float  # 0.0-1.0


_ASSESS_PROMPT = """You are reviewing accessibility-review dismissal notes
to find a recurring, consistent pattern. Below are real rationales an
independent reviewer wrote when dismissing findings Analyst flagged under
the same WCAG criterion, across different scans of different websites.

WCAG criterion: {criterion}

Dismissal rationales:
{rationales}

Is this a consistent, recurring false-positive pattern (the reviewer
reaching the same conclusion for the same underlying reason each time),
not just several unrelated dismissals that happen to share a WCAG
citation? If yes, describe the pattern in one sentence, specific enough
that someone could recognize it in a new finding. Give a confidence
between 0.0 and 1.0.
"""


def _normalize_criterion(criterion: str) -> str:
    """"1.3.1 Info and Relationships (A)" and "1.3.1" both cluster under
    "1.3.1" -- Analyst and Editor don't always cite a criterion identically
    even when they mean the same thing, and clustering on the full string
    would silently split one real pattern into several small ones.
    """
    return criterion.strip().split(" ")[0].rstrip(".")


def _pattern_key(criterion_code: str) -> str:
    return "learned-pattern-" + hashlib.sha256(criterion_code.encode()).hexdigest()[:16]


async def mine_patterns() -> list[dict]:
    """Clusters dismissed findings by WCAG criterion, asks Gemma to assess
    each cluster with enough volume, and escalates the consistent,
    high-confidence ones for SME review. Returns the newly-created
    escalations (empty if Ollama isn't reachable or nothing qualifies).
    """
    if not gemma_client.is_available():
        return []

    dismissed = fs.iter_dismissed_findings()
    by_criterion: dict[str, list[dict]] = defaultdict(list)
    for f in dismissed:
        code = _normalize_criterion(f["wcag_criterion"])
        if code:
            by_criterion[code].append(f)

    created = []
    for code, findings in by_criterion.items():
        if len(findings) < MIN_OCCURRENCES:
            continue
        key = _pattern_key(code)
        if fs.get_escalation(key) is not None:
            continue  # already mined (pending, confirmed, or dismissed) -- don't re-propose

        sample = findings[:SAMPLE_SIZE]
        rationales_text = "\n".join(f"{i + 1}. {f['rationale']}" for i, f in enumerate(sample))
        prompt = _ASSESS_PROMPT.format(criterion=code, rationales=rationales_text)
        assessment = await gemma_client.generate_structured(prompt, _PatternAssessment)

        if not assessment.is_consistent_pattern or assessment.confidence < CONFIDENCE_THRESHOLD:
            continue

        fs.create_escalation(
            key,
            {
                "kind": "learned_pattern",
                "wcag_criterion": code,
                "pattern_description": assessment.pattern_description,
                "confidence": assessment.confidence,
                "occurrence_count": len(findings),
                "sample_rationales": [f["rationale"] for f in sample],
            },
        )
        created.append({"wcag_criterion": code, "occurrence_count": len(findings)})

    return created


def resolve_pattern_escalation(escalation_id: str, disposition: str, reviewer: str = "sme") -> None:
    """SME disposition on a mined pattern. confirm -> becomes persistent
    memory (Editor's prompt reads it back on every future scan); dismiss ->
    the pattern was a false alarm from the miner itself, discarded.
    """
    if disposition not in ("confirm", "dismiss"):
        raise ValueError(f"disposition must be 'confirm' or 'dismiss', got {disposition!r}")

    data = fs.resolve_escalation(escalation_id, disposition=disposition, reviewer=reviewer)

    if disposition == "confirm":
        fs.save_learned_pattern(
            escalation_id,
            {
                "wcag_criterion": data["wcag_criterion"],
                "pattern_description": data["pattern_description"],
                "confidence": data["confidence"],
                "occurrence_count": data["occurrence_count"],
                "reviewer": reviewer,
            },
        )
