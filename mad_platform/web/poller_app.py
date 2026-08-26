"""HTTP entrypoint for scan-wcag-poller. Cloud Scheduler hits this on a
tick -- not a human. Not publicly reachable like scan-onboarding is; only
the Scheduler's dedicated invoker identity can call it.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from mad_platform.agents.wcag_auto_heal import run_wcag_freshness_check

# Not logging.basicConfig(): uvicorn configures its own logging on startup,
# after this module is imported, and silently drops INFO-level output from
# our own loggers on a cold start if we rely on basicConfig() alone --
# confirmed in production on scan-onboarding. Attaching a handler directly
# to the "mad_platform" namespace, independent of the root logger uvicorn
# manages, survives that.
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
_mad_logger = logging.getLogger("mad_platform")
_mad_logger.setLevel(logging.INFO)
_mad_logger.addHandler(_handler)
_mad_logger.propagate = False

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
