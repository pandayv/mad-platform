"""Manual CLI entry point for testing Step 1 (site -> findings) yourself.

Usage:
    .venv/bin/python run_scan.py https://example.com
    .venv/bin/python run_scan.py https://example.com --job-id <existing-job-id>   # resume

No frontend exists yet -- this is the way to exercise the real pipeline
(Orchestrator -> Analyst -> Editor, with Firestore checkpointing) until
there is one.
"""

from __future__ import annotations

import argparse
import asyncio

from mad_platform.agents.orchestrator import run_one_time_scan


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a one-time accessibility scan (Step 1).")
    parser.add_argument("url", help="URL to scan")
    parser.add_argument("--job-id", help="Resume an existing job instead of starting fresh")
    args = parser.parse_args()

    print(f"Scanning {args.url} ...")
    if args.job_id:
        print(f"(resuming job {args.job_id})")
    print("This calls real Gemini models and writes to the real Firestore database -- expect ~15-50s.\n")

    results = asyncio.run(run_one_time_scan(args.url, job_id=args.job_id))

    total_confirmed = total_dismissed = 0
    for page_url, findings in results.items():
        confirmed = [f for f in findings if f.confirmed]
        dismissed = [f for f in findings if not f.confirmed]
        total_confirmed += len(confirmed)
        total_dismissed += len(dismissed)

        print(f"\n{'=' * 78}")
        print(f"PAGE: {page_url}")
        print(f"{'=' * 78}")
        print(f"{len(confirmed)} confirmed, {len(dismissed)} dismissed by Editor\n")

        for f in confirmed:
            print(f"  [CONFIRMED] WCAG {f.wcag_criterion}  (confidence {f.confidence:.2f})")
            print(f"    {f.rationale}\n")

        if dismissed:
            print("  --- dismissed as false positives ---")
            for f in dismissed:
                print(f"  [DISMISSED] WCAG {f.wcag_criterion}: {f.rationale}\n")

    print(f"{'=' * 78}")
    print(f"TOTAL: {total_confirmed} confirmed findings, {total_dismissed} dismissed across {len(results)} page(s).")


if __name__ == "__main__":
    main()
