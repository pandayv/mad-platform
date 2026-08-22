"""Manual/demo entry point for the dismissal-pattern miner
(mad_platform/agents/pattern_miner.py) -- clusters Editor's real dismissal
history by WCAG criterion, asks a locally-run Gemma to assess each
cluster for a consistent pattern, and proposes the strong ones to the
existing SME review queue.

Requires Ollama running locally with the model pulled:
    ollama serve
    ollama pull gemma3:4b

Usage:
    .venv/bin/python mine_patterns.py
"""

from __future__ import annotations

import asyncio

from mad_platform.agents.pattern_miner import mine_patterns
from mad_platform.tools import gemma_client


async def main() -> None:
    if not gemma_client.is_available():
        print("Ollama isn't reachable at localhost:11434 -- start it with `ollama serve`.")
        return

    print("Mining Editor's dismissal history for recurring patterns...")
    created = await mine_patterns()

    if not created:
        print("No new pattern met the volume/confidence bar this run.")
        return

    print(f"\n{len(created)} pattern(s) proposed for SME review:")
    for c in created:
        print(f"  WCAG {c['wcag_criterion']} -- seen {c['occurrence_count']} time(s)")
    print("\nUse the /review queue to confirm or dismiss each one.")


if __name__ == "__main__":
    asyncio.run(main())
