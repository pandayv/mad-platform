"""HTTP entrypoint for scan-wcag-poller. Cloud Scheduler hits this on a
tick (gcp-deploy.sh section 11) -- not a human. NOT --allow-unauthenticated,
only the Scheduler's dedicated invoker identity can reach it
(REQUIREMENTS.md section 7.1), unlike scan-onboarding.
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
    result = run_wcag_freshness_check()
    logger.info("WCAG freshness check: %s", result)
    return result


@app.get("/")
async def health() -> dict:
    return {"status": "ok"}
