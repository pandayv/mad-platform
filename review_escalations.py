"""SME review queue -- CLI stand-in for the internal tool described in
REQUIREMENTS.md section 5.6 (not Jira, not email -- an internal-only
surface, since this is AccessScout's own quality control, not something
handed to the customer).

Usage:
    .venv/bin/python review_escalations.py --list
    .venv/bin/python review_escalations.py --resolve <escalation-id> --confirm
    .venv/bin/python review_escalations.py --resolve <escalation-id> --dismiss
"""

from __future__ import annotations

import argparse

from mad_platform.agents.action_agent import resolve_escalation
from mad_platform.agents.wcag_auto_heal import resolve_kb_escalation
from mad_platform.state import firestore_client as fs
from mad_platform.tools.issue_sink import MockIssueSink


def list_pending() -> None:
    """Two kinds of escalation share this one queue -- a finding routed by
    the section 5.6 gate, or a WCAG version change routed by the auto-heal
    loop's minor/major classification (Guiding Principle 3). Same
    human-approval mechanism, two different judgment calls behind it.
    """
    pending = fs.list_pending_escalations()
    if not pending:
        print("No pending escalations.")
        return
    print(f"{len(pending)} pending escalation(s):\n")
    for e in pending:
        print(f"ID: {e['id']}")
        if e.get("kind") == "kb_version_change":
            print(f"  Type: WCAG knowledge-base version change")
            print(f"  {e['old_version']} -> {e['new_version']}  (classified: {e['change_type']}, confidence={e['confidence']:.2f})")
            print(f"  Reasoning: {e['reasoning']}")
            print(f"  Why flagged: not a confident 'minor' classification -- needs review before re-embedding (Guiding Principle 3)")
        else:
            print(f"  Page: {e['page_url']}")
            print(f"  WCAG {e['wcag_criterion']}  severity={e['severity']}  confidence={e['editor_confidence']:.2f}")
            print(f"  Evidence: {e['editor_rationale']}")
            print(f"  Why flagged: low confidence and/or critical severity (REQUIREMENTS.md section 5.6)")
        print()


def resolve(escalation_id: str, disposition: str) -> None:
    escalation = next((e for e in fs.list_pending_escalations() if e["id"] == escalation_id), None)
    if escalation is None:
        print(f"No pending escalation with id {escalation_id!r}.")
        return

    if escalation.get("kind") == "kb_version_change":
        resolve_kb_escalation(escalation_id, disposition=disposition, reviewer="cli-review")
        if disposition == "confirm":
            print(f"Confirmed. Knowledge base re-embedded and advanced to {escalation['new_version']}.")
        else:
            print(f"Dismissed. Knowledge base stays on its current version -- corpus needs a real content update first.")
        return

    sink = MockIssueSink()  # real Jira credentials: SETUP.md item 18
    ticket = resolve_escalation(sink, escalation_id, disposition=disposition, reviewer="cli-review")
    if disposition == "confirm":
        print(f"Confirmed. Ticket filed: {ticket}")
    else:
        print("Dismissed. No ticket filed, this finding will not appear in the report.")


def main() -> None:
    parser = argparse.ArgumentParser(description="SME escalation queue.")
    parser.add_argument("--list", action="store_true", help="List pending escalations")
    parser.add_argument("--resolve", metavar="ESCALATION_ID", help="Resolve an escalation by id")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--confirm", action="store_true")
    group.add_argument("--dismiss", action="store_true")
    args = parser.parse_args()

    if args.list:
        list_pending()
    elif args.resolve:
        if not (args.confirm or args.dismiss):
            parser.error("--resolve requires --confirm or --dismiss")
        resolve(args.resolve, "confirm" if args.confirm else "dismiss")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
