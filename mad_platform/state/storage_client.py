"""Persists the generated report to Cloud Storage.

Per REQUIREMENTS.md section 7 -- "generated report artifacts" was always
part of the stated architecture for this bucket, it just hadn't been
wired up yet. Kept private (no public read access) -- these are real
findings about a specific site's compliance gaps, not something to expose
by default.
"""

from __future__ import annotations

import os

from google.cloud import storage

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-d7e6174e-cca7-4d16-9d5")
_BUCKET_NAME = "scan-storage-9747"

_client = storage.Client(project=_PROJECT)
_bucket = _client.bucket(_BUCKET_NAME)


def save_report(job_id: str, report_markdown: str) -> str:
    """Saves the report, returns its gs:// URI."""
    blob_path = f"reports/{job_id}.md"
    blob = _bucket.blob(blob_path)
    blob.upload_from_string(report_markdown, content_type="text/markdown")
    return f"gs://{_BUCKET_NAME}/{blob_path}"


def read_report(job_id: str) -> str | None:
    blob = _bucket.blob(f"reports/{job_id}.md")
    return blob.download_as_text() if blob.exists() else None
