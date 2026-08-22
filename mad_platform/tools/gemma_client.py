"""Structured calls to a locally-run Gemma model via Ollama.

Deliberately not Vertex AI -- the one job that calls this (pattern_miner's
periodic dismissal-pattern mining) is a background batch job with no
live-request latency pressure, exactly the workload Gemma's smaller
variants are built for (on-device/local inference, cost-efficient,
infrastructure you control). Every other LLM call in the pipeline is a
real-time, user-facing judgment -- those stay on Gemini via adk_client,
which is the right tool for that job. This is not a cost-cutting
substitute for Gemini; it's a second model used for the kind of workload
it's actually designed for.

Requires `ollama serve` running locally with the model already pulled
(`ollama pull gemma3:4b`) -- no fallback to a hosted API, since the whole
point is that this runs on infrastructure we control, not a managed one.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

import requests
from pydantic import BaseModel

_OLLAMA_URL = "http://localhost:11434/api/generate"
_MODEL = "gemma3:4b"
_TIMEOUT_S = 60

T = TypeVar("T", bound=BaseModel)


def _generate_sync(prompt: str, schema: type[BaseModel]) -> str:
    resp = requests.post(
        _OLLAMA_URL,
        json={"model": _MODEL, "prompt": prompt, "format": schema.model_json_schema(), "stream": False},
        timeout=_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()["response"]


async def generate_structured(prompt: str, schema: type[T]) -> T:
    """Same shape as adk_client.generate_structured (model, prompt, schema)
    minus the model argument -- there's only one local model configured,
    not a cache of them, since this client has exactly one caller.
    """
    text = await asyncio.to_thread(_generate_sync, prompt, schema)
    return schema.model_validate_json(text)


def is_available() -> bool:
    """Whether Ollama is actually reachable -- callers use this to skip
    mining gracefully (e.g. in an environment without Ollama running)
    rather than crash a batch job that has nothing else depending on it.
    """
    try:
        requests.get("http://localhost:11434/api/version", timeout=3).raise_for_status()
        return True
    except Exception:
        return False
