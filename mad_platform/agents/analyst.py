"""Analyst: checks one page for accessibility violations.

Per REQUIREMENTS.md section 5.4 step 2 and Guiding Principle 1 -- the rule
checks and the two AI-assisted checks are independent, order-irrelevant
work, so they run in PARALLEL, not sequentially. This is a deliberate
orchestration-pattern choice, not an implementation detail: there's no
reason to check contrast after checking alt text, or to wait for the
visual check before running the semantic one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from mad_platform.tools.ai_checks import AIFinding, run_semantic_check, run_visual_check
from mad_platform.tools.crawler import PageSnapshot
from mad_platform.tools.rule_checks import RuleFinding, run_all_rule_checks


@dataclass
class RawFinding:
    """Normalized shape for anything Analyst produces, regardless of
    whether it came from a deterministic rule or an AI-assisted check --
    Editor works against one consistent representation.
    """

    source: str  # "rule" or "ai_visual" or "ai_semantic"
    check: str
    wcag_criterion: str
    description: str
    selector: str
    analyst_confidence: float  # rule checks are 1.0 -- the rule matched, full stop


def _from_rule(f: RuleFinding) -> RawFinding:
    return RawFinding(
        source="rule",
        check=f.check,
        wcag_criterion=f.wcag_criterion,
        description=f.message,
        selector=f.selector,
        analyst_confidence=1.0,
    )


def _from_ai(source: str, f: AIFinding) -> RawFinding:
    return RawFinding(
        source=source,
        check=source,
        wcag_criterion=f.wcag_criterion,
        description=f.description,
        selector=f.selector,
        analyst_confidence=f.confidence,
    )


async def analyze_page(snapshot: PageSnapshot) -> list[RawFinding]:
    """Runs rule checks (sync, no API calls) and both AI-assisted checks
    (async, Gemini) concurrently, then normalizes everything into one list.
    """
    rule_task = asyncio.to_thread(run_all_rule_checks, snapshot)
    visual_task = asyncio.to_thread(run_visual_check, snapshot)
    semantic_task = asyncio.to_thread(run_semantic_check, snapshot)

    rule_findings, visual_findings, semantic_findings = await asyncio.gather(
        rule_task, visual_task, semantic_task
    )

    findings: list[RawFinding] = []
    findings += [_from_rule(f) for f in rule_findings]
    findings += [_from_ai("ai_visual", f) for f in visual_findings]
    findings += [_from_ai("ai_semantic", f) for f in semantic_findings]
    return findings


def analyze_page_sync(snapshot: PageSnapshot) -> list[RawFinding]:
    return asyncio.run(analyze_page(snapshot))
