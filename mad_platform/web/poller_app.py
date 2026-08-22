"""HTTP entrypoint for scan-wcag-poller. Cloud Scheduler hits this on a
tick -- not a human. Not publicly reachable like scan-onboarding is; only
the Scheduler's dedicated invoker identity can call it.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from mad_platform.agents.wcag_auto_heal import run_wcag_freshness_check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mad_platform.wcag_poller")

app = FastAPI(title="MAD Platform WCAG Poller")


@app.post("/")
async def tick() -> dict:
    result = await run_wcag_freshness_check()
    logger.info("WCAG freshness check: %s", result)
    return result


@app.get("/")
async def health() -> dict:
    return {"status": "ok"}
