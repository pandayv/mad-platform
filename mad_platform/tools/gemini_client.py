"""Thin wrapper around Vertex AI's Gemini client.

location='global' is required, not 'us-central1' -- confirmed 2026-08-21
(see REQUIREMENTS.md section 7): models appear in the regional catalog
listing but 404 when actually called there.

gemini-3.5-flash-lite for high-volume calls, gemini-3.7-flash for the
handful of judgment calls -- per REQUIREMENTS.md section 6.3. There is no
Pro-tier model at the "Gemini 3.5+" floor this project is required to use.
"""

from __future__ import annotations

import os
import time
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

FLASH_LITE = "gemini-3.5-flash-lite"
FLASH = "gemini-3.7-flash"
EMBEDDING_MODEL = "gemini-embedding-001"

# Caught for real: a single Vertex AI call hung indefinitely in production
# (select_pages, right after the entry-page crawl) with no timeout and no
# error -- the background task just sat there forever, invisible to the
# job's status (Firestore never got another checkpoint, and nothing raised
# for fs.fail_job to catch). The SDK's own default is no timeout at all.
#
# 30s was the first value tried; caught for real a second time against the
# demo site (22 findings across 3 pages): Reporter's ranking call processes
# every confirmed finding in one prompt and returns a bigger structured
# response than a small call like select_pages, and legitimately needs more
# than 30s once there are more than a handful of findings. Raised to 60s --
# still bounded (worst case ~2min with the one retry below), just sized for
# the biggest real call this pipeline makes rather than the smallest.
_TIMEOUT_MS = 60_000
_MAX_ATTEMPTS = 2

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-d7e6174e-cca7-4d16-9d5")
_client = genai.Client(
    vertexai=True, project=_PROJECT, location="global",
    http_options=types.HttpOptions(timeout=_TIMEOUT_MS),
)

T = TypeVar("T", bound=BaseModel)


def _with_retry(call):
    """One retry on a transient failure (including a timeout) -- bounded,
    not a loop, matching the retry pattern already used for page fetches
    (crawler.py) and the Orchestrator's own retry gate. A second failure
    is a real problem and should surface, not be swallowed.
    """
    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, matches crawler.py's approach
            last_error = exc
            if attempt < _MAX_ATTEMPTS:
                time.sleep(1.5 * attempt)
    raise last_error  # noqa: RSE102 - re-raising the last real exception, not a bare raise


def generate_structured(
    model: str,
    prompt: str,
    schema: type[T],
    image_bytes: bytes | None = None,
) -> T:
    """One structured-output Gemini call. Raises on malformed responses
    rather than returning something a caller might silently misuse --
    per REQUIREMENTS.md section 6.1, every LLM call must be schema-validated.
    """
    parts: list = []
    if image_bytes:
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type="image/png"))
    parts.append(prompt)

    def _call():
        response = _client.models.generate_content(
            model=model,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        return schema.model_validate_json(response.text)

    return _with_retry(_call)


def embed(text: str) -> list[float]:
    def _call():
        result = _client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
        return list(result.embeddings[0].values)

    return _with_retry(_call)
