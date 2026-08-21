"""Manual CLI entry point for testing the full core (Steps 1-4) yourself.

Usage:
    .venv/bin/python run_scan.py https://example.com
    .venv/bin/python run_scan.py https://example.com --job-id <existing-job-id>   # resume

No frontend exists yet -- this is the way to exercise the real pipeline
(Orchestrator -> Analyst -> Editor -> Reporter -> Action Agent, with
Firestore checkpointing) until there is one. Files tickets against a
MockIssueSink by default -- real Jira credentials are a separate setup
step (SETUP.md item 18), not required to run this.
"""

from __future__ import annotations

import argparse
import asyncio

from mad_platform.agents.orchestrator import run_one_time_scan


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a one-time accessibility scan (core, Steps 1-4).")
    parser.add_argument("url", help="URL to scan")
    parser.add_argument("--job-id", help="Resume an existing job instead of starting fresh")
    args = parser.parse_args()

    print(f"Scanning {args.url} ...")
    if args.job_id:
        print(f"(resuming job {args.job_id})")
    print("This calls real Gemini models and writes to the real Firestore database -- expect ~30-90s.\n")

    result = asyncio.run(run_one_time_scan(args.url, job_id=args.job_id))

    print(f"{'=' * 78}")
    print("PER-PAGE DETECTION")
    print(f"{'=' * 78}")
    for page_url, findings in result.findings_by_page.items():
        confirmed = [f for f in findings if f.confirmed]
        dismissed = [f for f in findings if not f.confirmed]
        print(f"\n{page_url}")
        print(f"  {len(confirmed)} confirmed, {len(dismissed)} dismissed by Editor")

    print(f"\n{'=' * 78}")
    print("REPORT")
    print(f"{'=' * 78}")
    print(result.report)

    print(f"\n{'=' * 78}")
    print("ACTION AGENT")
    print(f"{'=' * 78}")
    print(f"Filed autonomously: {len(result.filed)}")
    for _i, f, ticket in result.filed:
        print(f"  {ticket}: [{f.severity}] WCAG {f.wcag_criterion}")
    print(f"Escalated to SME queue (no ticket yet): {len(result.escalated)}")
    for _i, f, escalation_id in result.escalated:
        print(f"  {escalation_id}: [{f.severity}] WCAG {f.wcag_criterion} (confidence {f.editor_confidence:.2f})")
    if result.already_filed:
        print(f"Already filed (idempotency, from a prior run): {len(result.already_filed)}")

    if result.escalated:
        print(
            f"\n{len(result.escalated)} finding(s) are waiting on SME review. "
            f"Use review_escalations.py to resolve them."
        )


if __name__ == "__main__":
    main()
