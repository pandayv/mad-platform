"""Minimal RAG over the WCAG corpus -- grounds citations against the real
standard instead of the model's unaided claim. Freshness/refresh logic
lives separately in wcag_auto_heal.py; this module only embeds and
retrieves.

Storage is Firestore, not a dedicated vector database, since the corpus is
small (~18 curated criteria) and mostly static -- a dedicated always-on
vector index isn't justified at this size. Retrieval caches the embedded
corpus in memory after first load rather than re-fetching from Firestore
on every call -- an implementation detail, not a deviation from Firestore
being the durable source of truth.
"""

from __future__ import annotations

import math
import os

from google.cloud import firestore

from mad_platform.data.wcag_corpus import WCAG_CORPUS, WCAGCriterion
from mad_platform.tools.gemini_client import embed as _embed

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-d7e6174e-cca7-4d16-9d5")
_DATABASE = "scan-firestore"
_client_fs = firestore.Client(project=_PROJECT, database=_DATABASE)
_KB = _client_fs.collection("wcag_knowledge_base")

_cache: list[tuple[WCAGCriterion, list[float]]] | None = None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def embed_and_store_corpus() -> int:
    """One-time setup: embeds every criterion and stores it in Firestore.

    Safe to re-run -- overwrites existing entries by document id (the
    criterion number), doesn't duplicate.
    """
    count = 0
    for criterion in WCAG_CORPUS:
        text = f"{criterion.number} {criterion.title}: {criterion.description}"
        embedding = _embed(text)
        _KB.document(criterion.number.replace(".", "_")).set(
            {
                "number": criterion.number,
                "title": criterion.title,
                "level": criterion.level,
                "description": criterion.description,
                "embedding": embedding,
            }
        )
        count += 1
    global _cache
    _cache = None  # force reload on next retrieval
    return count


def _load_cache() -> list[tuple[WCAGCriterion, list[float]]]:
    global _cache
    if _cache is not None:
        return _cache
    loaded = []
    for doc in _KB.stream():
        d = doc.to_dict()
        criterion = WCAGCriterion(
            number=d["number"], title=d["title"], level=d["level"], description=d["description"]
        )
        loaded.append((criterion, d["embedding"]))
    _cache = loaded
    return _cache


def retrieve(query: str, top_k: int = 1) -> list[WCAGCriterion]:
    """Embeds the query and returns the top_k most similar WCAG criteria
    from the stored corpus, by cosine similarity.
    """
    corpus = _load_cache()
    if not corpus:
        return []
    query_embedding = _embed(query)
    scored = [(_cosine_similarity(query_embedding, emb), criterion) for criterion, emb in corpus]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [criterion for _, criterion in scored[:top_k]]
