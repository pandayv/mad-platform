"""Persists generated report artifacts to Cloud Storage.

Per REQUIREMENTS.md section 7 -- "generated report artifacts" was always
part of the stated architecture for this bucket, it just hadn't been
wired up yet. Kept private (no public read access) -- these are real
findings about a specific site's compliance gaps, not something to expose
by default. Access for now is the Cloud Console browser link below, which
works for anyone with viewer access on the project (i.e. us, right now);
a real "email this to the site owner" flow would need signed URLs instead,
which need a service account key or IAM SignBlob permission we don't have
configured yet -- a disclosed gap, not solved here.
"""

from __future__ import annotations

import os

from google.cloud import storage

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "project-d7e6174e-cca7-4d16-9d5")
_BUCKET_NAME = "scan-storage-9747"

_client = storage.Client(project=_PROJECT)
_bucket = _client.bucket(_BUCKET_NAME)

_CONTENT_TYPES = {
    "md": "text/markdown",
    "html": "text/html",
    "pdf": "application/pdf",
}


def save_report_bundle(job_id: str, markdown: str, html: str, pdf_bytes: bytes) -> dict[str, str]:
    """Saves all three formats, returns {"md": gs://..., "html": gs://..., "pdf": gs://...}."""
    contents = {"md": markdown, "html": html, "pdf": pdf_bytes}
    uris: dict[str, str] = {}
    for ext, content in contents.items():
        blob_path = f"reports/{job_id}.{ext}"
        blob = _bucket.blob(blob_path)
        blob.upload_from_string(content, content_type=_CONTENT_TYPES[ext])
        uris[ext] = f"gs://{_BUCKET_NAME}/{blob_path}"
    return uris


def read_report(job_id: str, ext: str = "html") -> str | bytes | None:
    blob = _bucket.blob(f"reports/{job_id}.{ext}")
    if not blob.exists():
        return None
    return blob.download_as_bytes() if ext == "pdf" else blob.download_as_text()


def console_object_url(job_id: str, ext: str = "html") -> str:
    """Cloud Console link to the specific report object -- opens a preview/
    download UI, requires the viewer to be logged into the GCP project.
    """
    return f"https://console.cloud.google.com/storage/browser/_details/{_BUCKET_NAME}/reports/{job_id}.{ext}?project={_PROJECT}"


def console_folder_url() -> str:
    """Cloud Console link to the whole reports/ folder -- the standing,
    reusable link the user can bookmark rather than one per scan.
    """
    return f"https://console.cloud.google.com/storage/browser/{_BUCKET_NAME}/reports?project={_PROJECT}"
