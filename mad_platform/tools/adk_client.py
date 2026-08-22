"""Structured Gemini calls via ADK's LlmAgent + Runner.

Every judgment-bearing LLM call in this pipeline goes through this module
now, not the raw google-genai SDK -- the actual "agent" decision points
(page selection, verification, ranking, retry/escalation judgment, WCAG
version classification) now run as real ADK agents, invoked through
ADK's own Runner rather than a bare API client call.

Embeddings stay on the raw SDK (gemini_client.py) -- ADK's agent model
has no embedding-agent primitive, since computing a vector isn't a
judgment call an agent makes, it's a deterministic tool operation. That
boundary matches the tool-vs-agent split used throughout this codebase.

Same external shape as the module it replaces (one prompt in, one
validated Pydantic object out via generate_structured), so callers change
only in how they reach Gemini, not what they ask for -- except this
version is async, since ADK's own docs are explicit that the sync Runner
interface ("run()") is for local testing only, not production use.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import TypeVar

from google.adk.agents.llm_agent import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel

# Same Vertex AI / location requirements as gemini_client.py -- set here
# too since ADK reads its model config from the environment rather than
# an explicit client object the way the raw SDK does.
os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "1")
os.environ.setdefault(
    "GOOGLE_CLOUD_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", "project-d7e6174e-cca7-4d16-9d5")
)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

_APP_NAME = "mad_platform"
_USER_ID = "mad_platform"

# Same values as gemini_client.py, same reasoning: no call in this
# pipeline should be able to hang or fail silently.
_TIMEOUT_S = 60
_MAX_ATTEMPTS = 2

T = TypeVar("T", bound=BaseModel)

# One LlmAgent (and its Runner) per (model, schema) pair, reused across
# calls -- agent construction has real overhead and the config is static
# per call site, only the prompt content and session change per call.
_runner_cache: dict[tuple[str, type], InMemoryRunner] = {}


def _get_runner(model: str, schema: type[BaseModel]) -> InMemoryRunner:
    key = (model, schema)
    if key not in _runner_cache:
        agent = LlmAgent(
            model=model,
            name=f"agent_{schema.__name__.lstrip('_').lower()}",
            instruction=(
                "Follow the user's instructions precisely and respond only in "
                "the required structured schema."
            ),
            output_schema=schema,
        )
        _runner_cache[key] = InMemoryRunner(agent=agent, app_name=_APP_NAME)
    return _runner_cache[key]


async def _run_once(runner: InMemoryRunner, prompt: str, image_bytes: bytes | None) -> str:
    session_id = str(uuid.uuid4())
    await runner.session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)

    parts: list[types.Part] = []
    if image_bytes:
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type="image/png"))
    parts.append(types.Part(text=prompt))
    message = types.Content(role="user", parts=parts)

    async def _collect() -> str:
        final_text: str | None = None
        async for event in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=message):
            if event.content and event.content.parts:
                for p in event.content.parts:
                    if getattr(p, "text", None):
                        final_text = p.text
        if final_text is None:
            raise RuntimeError("ADK agent produced no text output")
        return final_text

    return await asyncio.wait_for(_collect(), timeout=_TIMEOUT_S)


async def generate_structured(
    model: str,
    prompt: str,
    schema: type[T],
    image_bytes: bytes | None = None,
) -> T:
    """One structured-output call through an ADK agent. Bounded timeout,
    one retry -- a second failure is real and should surface, matching
    the pattern already used in gemini_client.py and crawler.py.
    """
    runner = _get_runner(model, schema)

    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            text = await _run_once(runner, prompt, image_bytes)
            return schema.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, matches gemini_client.py
            last_error = exc
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(1.5 * attempt)
    raise last_error  # noqa: RSE102
