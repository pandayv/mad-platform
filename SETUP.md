# MAD Platform — Setup Guide

Steps to get from zero to able to run this project, grouped by dependency
order.

**Which Google property is which** (this trips people up):
- **Google Cloud Console** (`console.cloud.google.com`) — "GCP" itself:
  project, billing, Vertex AI, Cloud Run, Firestore, IAM. Almost everything
  below lives here.
- **Google AI Studio** (`aistudio.google.com`) — lighter-weight Gemini API
  key prototyping, not tied to a GCP project. Optional if going the Vertex
  AI route (recommended — see step 6).
- **Google Developer Program** (`developers.google.com/program/gear`) —
  where GEAR training lives. Unrelated to the GCP project itself.

---

## GCP project setup

1. Create a GCP project and enable billing.
2. Enable the required APIs: Cloud Run, Firestore, Pub/Sub, Secret
   Manager, Cloud Storage, Vertex AI, Cloud Scheduler.
3. Set budget alerts per service, not just one project-wide budget — a
   runaway single service triggers its own alert rather than waiting for
   combined spend to cross one line.
4. Create a Firestore database (Native mode) — this project uses a
   non-default database name (`scan-firestore`), not the client library's
   default. Passing `database=` explicitly is easy to forget and silently
   connects to an empty database if missed.
5. Provision infrastructure via `gcp-deploy.sh` in this repo (run in Cloud
   Shell to avoid local auth friction) — idempotent, safe to re-run. Use
   `gcp-cleanup.sh` first if re-running against a partially-set-up
   project. This provisions 4 Cloud Run services, Firestore, Pub/Sub with
   a dead-lettered push subscription, 6 least-privilege service accounts,
   and Cloud Scheduler.
6. Enable Vertex AI (not just Google AI Studio) — `aiplatform.googleapis.com`.
   Confirm which Gemini models are actually available in your project via
   `client.models.list()`; model availability varies by project.

## Local dev environment

7. Python 3.10+ is required by the ADK toolchain; a modern 3.x install is
   recommended. Create a venv at `.venv/` in the repo root.
8. Install dependencies: `pip install -r requirements.txt`.
9. Install Playwright's browser binary: `playwright install --with-deps
   chromium` — the `--with-deps` form matters when building inside Docker
   too (see `Dockerfile`).
10. Vertex AI client location must be `global`, not a specific region like
    `us-central1` — some models appear in a region's catalog listing but
    404 when actually called there. This is independent of which region
    Cloud Run itself deploys to.
11. Docker isn't required locally — `gcloud builds submit` / `gcloud run
    deploy --source` build remotely via Cloud Build.

## Third-party accounts (for full functionality)

12. Jira Cloud (free tier) — for real ticket filing. Without this, the
    pipeline runs against a mock ticket sink, which is sufficient for
    development and testing.
13. Email sending (e.g. SendGrid free tier, or a dedicated account +
    app password for SMTP) — for report delivery by email, not yet wired
    up in this build.

## Optional

14. GEAR / Google Developer Program — free ADK ramp-up training, only
    useful if you're new to ADK: https://developers.google.com/program/gear
