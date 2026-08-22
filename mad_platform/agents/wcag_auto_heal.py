"""WCAG auto-heal: freshness-check + refresh loop for the shared knowledge
base. Tunes the system's knowledge, not its judgment.

Scope, deliberately: this detects a version change and decides whether to
auto-refresh or escalate for human review -- it does not dynamically fetch
and ingest new WCAG success-criteria text from W3C. Full WCAG spec
coverage is intentionally out of scope; "refresh" here means re-embedding
the existing curated corpus (mad_platform/data/wcag_corpus.py) and
updating the stored version pointer. That demonstrates the real decision
mechanism -- detect, classify, auto-act or escalate -- without pretending
to keep pace with the full spec's content, which is a much bigger problem.

Minor-vs-major classification leans on the model's own general knowledge
of WCAG's versioning history (2.0 -> 2.1 -> 2.2 are documented, publicly
well-known additive revisions within the same conformance model; a jump
to WCAG 3.0 is documented as a structurally different scoring model) --
this is stable, publicly established domain knowledge, not something that
needs a freshly-scraped changelog to get right.
"""

from __future__ import annotations

from pydantic import BaseModel

from mad_platform.state import firestore_client as fs
from mad_platform.tools.gemini_client import FLASH, generate_structured
from mad_platform.tools.rag import embed_and_store_corpus
from mad_platform.tools.wcag_version import fetch_current_wcag_version

_MINOR_AUTO_REFRESH_THRESHOLD = 0.8


class _VersionChangeClassification(BaseModel):
    change_type: str  # "minor" | "major"
    confidence: float
    reasoning: str


_CLASSIFY_PROMPT = """The WCAG accessibility standard's current version has
changed from {old_version} to {new_version}. Classify this change:

- "minor": the new version adds success criteria but keeps the same
  conformance model as the old version (e.g. WCAG 2.0 -> 2.1 -> 2.2 are all
  additive, backward-compatible revisions within the WCAG 2.x family --
  nothing that was previously compliant becomes non-compliant, and
  existing success-criterion numbers keep the same meaning).
- "major": the new version uses a fundamentally different conformance
  model (e.g. any jump to WCAG 3.0, which replaces binary pass/fail
  success criteria with a different scoring model entirely).

Give a confidence (0-1) and a one-sentence reason.
"""


def classify_version_change(old_version: str, new_version: str) -> _VersionChangeClassification:
    prompt = _CLASSIFY_PROMPT.format(old_version=old_version, new_version=new_version)
    return generate_structured(FLASH, prompt, _VersionChangeClassification)


def run_wcag_freshness_check(simulate_current_version: str | None = None) -> dict:
    """The scheduled freshness-check tick.

    simulate_current_version overrides the real W3C fetch -- demo/test
    only (see check_wcag_version.py --simulate). A real WCAG version
    change is rare (years apart), so waiting for one to naturally occur
    isn't practical for development or a recorded demo; this lets the
    decision mechanism itself be exercised on demand without faking any
    HTTP response.
    """
    stored = fs.get_kb_version()
    stored_version = stored.get("version") if stored else None

    current_version = simulate_current_version or fetch_current_wcag_version()
    fs.touch_kb_check(current_version)

    if stored_version == current_version:
        return {"action": "no_change", "version": current_version}

    if stored_version is None:
        # First run ever -- nothing to compare against yet, just record it.
        # embed_and_store_corpus() is assumed to have already been run once
        # during setup to seed the embeddings themselves.
        fs.set_kb_version(current_version)
        return {"action": "initialized", "version": current_version}

    classification = classify_version_change(stored_version, current_version)

    if classification.change_type == "minor" and classification.confidence >= _MINOR_AUTO_REFRESH_THRESHOLD:
        embed_and_store_corpus()
        fs.set_kb_version(current_version)
        return {
            "action": "auto_refreshed",
            "old_version": stored_version,
            "new_version": current_version,
            "reasoning": classification.reasoning,
        }

    key = f"kb-version-{stored_version}-to-{current_version}"
    fs.create_escalation(
        key,
        {
            "kind": "kb_version_change",
            "old_version": stored_version,
            "new_version": current_version,
            "change_type": classification.change_type,
            "confidence": classification.confidence,
            "reasoning": classification.reasoning,
        },
    )
    return {
        "action": "escalated",
        "old_version": stored_version,
        "new_version": current_version,
        "escalation_id": key,
        "reasoning": classification.reasoning,
    }


def resolve_kb_escalation(escalation_id: str, disposition: str, reviewer: str = "sme") -> None:
    """SME disposition on a pending kb_version_change escalation. confirm ->
    re-embeds the existing curated corpus and advances the stored version
    pointer now; dismiss -> stays on the old version, consciously (an SME
    judged the corpus itself needs a real content update first -- e.g. a
    genuine WCAG 3.0 jump -- before it's safe to just re-embed and move on).
    """
    if disposition not in ("confirm", "dismiss"):
        raise ValueError(f"disposition must be 'confirm' or 'dismiss', got {disposition!r}")

    data = fs.resolve_escalation(escalation_id, disposition=disposition, reviewer=reviewer)

    if disposition == "confirm":
        embed_and_store_corpus()
        fs.set_kb_version(data["new_version"])
