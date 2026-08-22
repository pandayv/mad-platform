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

## Deploy the Gemma pattern-miner (Cloud Run Job)

This is the one piece not covered by `gcp-deploy.sh` — a self-hosted
Gemma model (Ollama, not Vertex AI) that periodically mines Editor's
dismissal history for recurring false-positive patterns. Exact commands,
run from the repo root with `gcloud` authenticated against your project:

```bash
# 1. Service account -- Firestore access only, no Vertex AI needed.
gcloud iam service-accounts create pattern-miner-sa \
  --display-name="Pattern Miner (Gemma dismissal-pattern batch job)"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:pattern-miner-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

# 2. Build the image -- bakes gemma3:4b into the image at build time
#    (~5-10 min, slower than the other two images here on purpose, see
#    Dockerfile.pattern_miner).
gcloud builds submit --config=cloudbuild.pattern_miner.yaml --region=us-central1 \
  --substitutions=_IMAGE="us-central1-docker.pkg.dev/YOUR_PROJECT_ID/mad-platform/pattern-miner:latest" .

# 3. Create the Cloud Run Job (run-to-completion, not a Service).
gcloud run jobs create pattern-miner \
  --image=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/mad-platform/pattern-miner:latest \
  --region=us-central1 \
  --service-account=pattern-miner-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --memory=4Gi --cpu=4 --task-timeout=600 --max-retries=0

# 4. Let the existing Scheduler-invoker SA trigger this job too.
gcloud run jobs add-iam-policy-binding pattern-miner --region=us-central1 \
  --member="serviceAccount:scan-scheduler-invoker-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# 5. Weekly Cloud Scheduler trigger (dismissal history accumulates slowly
#    relative to scan volume -- no need to run this more often).
gcloud scheduler jobs create http pattern-miner-tick \
  --location=us-central1 \
  --schedule="0 3 * * 0" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/pattern-miner:run" \
  --http-method=POST \
  --oauth-service-account-email=scan-scheduler-invoker-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
```

To see it run immediately rather than waiting for the schedule:
`gcloud run jobs execute pattern-miner --region=us-central1 --wait`, then
check the output at `gcloud run jobs executions list --job=pattern-miner
--region=us-central1` or in Cloud Logging.

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
    development and testing. Requires an API token
    (`id.atlassian.com/manage-profile/security/api-tokens`), the site
    URL, account email, and project key; set `JIRA_URL`, `JIRA_EMAIL`,
    `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` (Cloud Run deploys these from
    Secret Manager — see `mad_platform/tools/issue_sink.py`).
13. Slack (free workspace) — for real-time alerts and scan-complete
    summaries. Create an Incoming Webhook
    (`api.slack.com/apps` → your app → Incoming Webhooks) and set
    `SLACK_WEBHOOK_URL`. Without this, notifications are silently
    skipped (`mad_platform/tools/notify.py`) — the pipeline itself
    doesn't depend on it.

## Optional

14. GEAR / Google Developer Program — free ADK ramp-up training, only
    useful if you're new to ADK: https://developers.google.com/program/gear
