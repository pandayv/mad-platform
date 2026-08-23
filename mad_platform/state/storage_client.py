"""Persists the generated report to Cloud Storage.

Kept private (no public read access) -- these are real findings about a
specific site's compliance gaps, not something to expose by default.
Access for now is the Cloud Console browser link below, which works for
anyone with viewer access on the project; a real "email this to the site
owner" flow would need signed URLs instead, which need a service account
key or IAM SignBlob permission not yet configured.
"""

from __future__ import annotations

import os

from google.cloud import storage

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-d7e6174e-cca7-4d16-9d5")
_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "scan-storage-9747")

_client = storage.Client(project=_PROJECT)
_bucket = _client.bucket(_BUCKET_NAME)


def save_report(job_id: str, report_html: str) -> str:
    """Saves the report, returns its gs:// URI."""
    blob_path = f"reports/{job_id}.html"
    blob = _bucket.blob(blob_path)
    blob.upload_from_string(report_html, content_type="text/html")
    return f"gs://{_BUCKET_NAME}/{blob_path}"


def read_report(job_id: str) -> str | None:
    blob = _bucket.blob(f"reports/{job_id}.html")
    return blob.download_as_text() if blob.exists() else None


def console_object_url(job_id: str) -> str:
    """Cloud Console link to the specific report object -- opens a preview/
    download UI, requires the viewer to be logged into the GCP project.
    """
    return f"https://console.cloud.google.com/storage/browser/_details/{_BUCKET_NAME}/reports/{job_id}.html?project={_PROJECT}"


def console_folder_url() -> str:
    """Cloud Console link to the whole reports/ folder -- the standing,
    reusable link the user can bookmark rather than one per scan.
    """
    return f"https://console.cloud.google.com/storage/browser/{_BUCKET_NAME}/reports?project={_PROJECT}"
