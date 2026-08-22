"""Thin wrapper around Vertex AI's Gemini client.

location='global' is required, not a specific region like 'us-central1':
some models appear in a region's catalog listing but 404 when actually
called there.

gemini-3.5-flash-lite for high-volume calls, gemini-3.7-flash for the
handful of judgment calls. No Pro-tier model exists at the "Gemini 3.5+"
floor this project targets, so the tiering is Flash-lite vs. Flash rather
than the more typical Flash-vs-Pro split.
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

# The SDK's own default is no timeout at all, so a single stalled call can
# hang a background task indefinitely with no error and no way for a
# caller to detect or recover from it. 60s is sized for the largest real
# call this pipeline makes (Reporter's ranking call, which processes every
# confirmed finding in one prompt) rather than the smallest -- still
# bounded (worst case ~2min with the one retry below), just not so tight
# that a legitimately larger request gets killed early.
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
    every LLM call in this pipeline is schema-validated, not free text.
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
