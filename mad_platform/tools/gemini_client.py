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
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

FLASH_LITE = "gemini-3.5-flash-lite"
FLASH = "gemini-3.7-flash"

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-d7e6174e-cca7-4d16-9d5")
_client = genai.Client(vertexai=True, project=_PROJECT, location="global")

T = TypeVar("T", bound=BaseModel)


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

    response = _client.models.generate_content(
        model=model,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    return schema.model_validate_json(response.text)
