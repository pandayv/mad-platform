"""Slack notifications: fire-and-forget webhook posts.

Two flavors, visually distinct in the channel by color bar and icon --
alert() for anything that needs a human to look now (an escalated
finding, a KB version change awaiting review), summary() for a completed
scan's results, informational rather than actionable. Both post through
the same webhook (one webhook is bound to one channel already), so the
distinction lives in the message, not the routing.

Best-effort: a Slack outage or missing webhook must never break the
pipeline it's reporting on, so failures are logged and swallowed, not
raised.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("mad_platform.notify")

_TIMEOUT_S = 10
_ALERT_COLOR = "#B91C1C"
_SUMMARY_COLOR = "#2563EB"


def _post(payload: dict) -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        resp = requests.post(webhook_url, json=payload, timeout=_TIMEOUT_S)
        resp.raise_for_status()
    except Exception:
        logger.exception("Slack notification failed")


def _send(icon: str, color: str, title: str, lines: list[str]) -> None:
    text = f"{icon} *{title}*\n" + "\n".join(f"• {line}" for line in lines)
    _post({"attachments": [{"color": color, "text": text}]})


def alert(title: str, lines: list[str]) -> None:
    """Needs a human now -- an escalated finding or a KB version change
    awaiting review.
    """
    _send(":rotating_light:", _ALERT_COLOR, title, lines)


def summary(title: str, lines: list[str]) -> None:
    """A completed scan's results -- informational, not actionable on its
    own.
    """
    _send(":bar_chart:", _SUMMARY_COLOR, title, lines)
