"""IssueSink: the ticketing abstraction Action Agent files through.

The filing logic sits behind this interface so an additional tracker
could be added later without changing Orchestrator or Reporter.
JiraIssueSink is the real implementation; MockIssueSink lets the pipeline
be tested end to end before real Jira credentials exist -- the
abstraction is what makes that possible without blocking on account setup.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

import requests


class IssueSink(ABC):
    @abstractmethod
    def create_issue(self, title: str, description: str) -> str:
        """Creates an issue, returns an external ticket ID/URL."""


class JiraIssueSink(IssueSink):
    def __init__(self) -> None:
        self.base_url = os.environ["JIRA_URL"].rstrip("/")
        self.email = os.environ["JIRA_EMAIL"]
        self.api_token = os.environ["JIRA_API_TOKEN"]
        self.project_key = os.environ["JIRA_PROJECT_KEY"]

    def create_issue(self, title: str, description: str) -> str:
        resp = requests.post(
            f"{self.base_url}/rest/api/2/issue",
            auth=(self.email, self.api_token),
            json={
                "fields": {
                    "project": {"key": self.project_key},
                    "summary": title,
                    "description": description,
                    "issuetype": {"name": "Task"},
                }
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["key"]


class MockIssueSink(IssueSink):
    """For testing without live Jira credentials -- keeps created issues
    in memory so tests can assert against them.
    """

    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []

    def create_issue(self, title: str, description: str) -> str:
        ticket_id = f"MOCK-{len(self.created) + 1}"
        self.created.append((ticket_id, title, description))
        return ticket_id
