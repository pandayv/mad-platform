#!/usr/bin/env bash
# MAD Platform — GCP scaffolding via plain gcloud. Run this in Cloud Shell
# (console.cloud.google.com, top-right terminal icon) to avoid local
# auth/environment friction entirely.
#
# Run section by section, not necessarily all at once — read each comment
# before running that block. Placeholder container images are used where a
# service's real code isn't deployed yet; swap in a real built image once
# it exists (see the bottom of this script for the actual deploy commands).
#
# Run gcp-cleanup.sh first if re-running this against a partially-set-up
# project — this script assumes a clean slate for everything except
# Firestore, the bucket, and the two pre-existing secrets referenced below.

set -euo pipefail

PROJECT_ID="your-project-id-here"   # <-- EDIT THIS
REGION="us-central1"
PLACEHOLDER_IMAGE="us-docker.pkg.dev/cloudrun/container/hello"

# If these already exist in your project, reuse the names below. Starting
# genuinely fresh: uncomment the two `gcloud create` lines below and this
# block becomes a real bucket/database name of your choosing instead.
FIRESTORE_DATABASE_ID="scan-firestore"
BUCKET_NAME="scan-storage-9747"

gcloud config set project "$PROJECT_ID"

# ── 1. Enable required APIs ────────────────────────────────────────────────
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  cloudscheduler.googleapis.com

# ── 2. Firestore (Native mode) — SKIPPED, already exists as $FIRESTORE_DATABASE_ID
# gcloud firestore databases create --database="$FIRESTORE_DATABASE_ID" --location="$REGION" --type=firestore-native
echo "Using existing Firestore database: $FIRESTORE_DATABASE_ID"
echo "NOTE for later: application code must connect with database=\"$FIRESTORE_DATABASE_ID\","
echo "not the client library default — it's not named (default)."

# ── 3. Cloud Storage bucket — SKIPPED, already exists as $BUCKET_NAME
# gcloud storage buckets create "gs://${BUCKET_NAME}" --location="$REGION"
echo "Using existing bucket: gs://${BUCKET_NAME}"

# ── 4. Secret Manager — SKIPPED, already exist: github-webhook-secret,
#    jira-email-secrets (one combined secret for both Jira and email
#    creds, not split into two).
#    Add real values via `gcloud secrets versions add SECRET --data-file=-`
#    (reads stdin, keeps the value out of shell history) or the Console UI.
#    Don't put real secret values directly in this script or any file you commit.
echo "Using existing secrets: github-webhook-secret, jira-email-secrets"

# ── 5. Service accounts — one identity per service (what it can READ/WRITE),
#    plus two SEPARATE identities for who's allowed to INVOKE a service.
#    Keep this distinction — collapsing invoker and resource identities
#    into one account each reintroduces exactly the kind of circular/
#    over-broad access this split exists to avoid.
#    Tolerant of "already exists" (e.g. a rate limit stopped a prior run
#    partway through) so re-running this whole script is always safe.
for sa in scan-onboarding scan-github-trigger scan-wcag-poller scan-orchestrator scan-pubsub-invoker scan-scheduler-invoker; do
  gcloud iam service-accounts create "${sa}-sa" --display-name="${sa}" \
    && echo "created: ${sa}-sa" \
    || echo "skipped (already exists): ${sa}-sa"
done

SA_ONBOARDING="scan-onboarding-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SA_GITHUB="scan-github-trigger-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SA_WCAG="scan-wcag-poller-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SA_ORCH="scan-orchestrator-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SA_PUBSUB_INVOKER="scan-pubsub-invoker-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SA_SCHEDULER_INVOKER="scan-scheduler-invoker-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# ── 6. Pub/Sub — main topic + dead-letter topic ────────────────────────────
for topic in review-cycle-requests review-cycle-requests-dlq; do
  gcloud pubsub topics create "$topic" \
    && echo "created topic: $topic" \
    || echo "skipped (already exists): $topic"
done

# ── 7. Deploy the four Cloud Run services (placeholder image for now) ──────
# scan-onboarding: public, business owner hits this directly.
gcloud run deploy scan-onboarding \
  --image="$PLACEHOLDER_IMAGE" --region="$REGION" \
  --service-account="$SA_ONBOARDING" \
  --min-instances=0 --allow-unauthenticated

# scan-github-trigger: public (GitHub's servers must reach it), but the
# APPLICATION verifies the webhook signature — that's the real security
# boundary here, not Cloud Run IAM.
gcloud run deploy scan-github-trigger \
  --image="$PLACEHOLDER_IMAGE" --region="$REGION" \
  --service-account="$SA_GITHUB" \
  --min-instances=0 --allow-unauthenticated

# scan-wcag-poller: NOT public — only Cloud Scheduler's dedicated invoker
# identity should reach it. No --allow-unauthenticated.
gcloud run deploy scan-wcag-poller \
  --image="$PLACEHOLDER_IMAGE" --region="$REGION" \
  --service-account="$SA_WCAG" \
  --min-instances=0

# scan-orchestrator: NOT public — only the Pub/Sub push invoker identity
# should reach it. No --allow-unauthenticated.
gcloud run deploy scan-orchestrator \
  --image="$PLACEHOLDER_IMAGE" --region="$REGION" \
  --service-account="$SA_ORCH" \
  --min-instances=0

# ── 8. IAM — grant each service's OWN account only what IT needs ──────────
# Firestore (all four services need it)
for sa in "$SA_ONBOARDING" "$SA_GITHUB" "$SA_WCAG" "$SA_ORCH"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${sa}" --role="roles/datastore.user"
done

# Pub/Sub publish — ONLY the three trigger services, and ONLY publisher,
# never subscriber
for sa in "$SA_ONBOARDING" "$SA_GITHUB" "$SA_WCAG"; do
  gcloud pubsub topics add-iam-policy-binding review-cycle-requests \
    --member="serviceAccount:${sa}" --role="roles/pubsub.publisher"
done

# Vertex AI — only the two services that actually call Gemini
for sa in "$SA_WCAG" "$SA_ORCH"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${sa}" --role="roles/aiplatform.user"
done

# Secrets — scoped to the SPECIFIC secret, not project-wide
gcloud secrets add-iam-policy-binding github-webhook-secret \
  --member="serviceAccount:${SA_GITHUB}" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding jira-email-secrets \
  --member="serviceAccount:${SA_ORCH}" --role="roles/secretmanager.secretAccessor"

# Cloud Storage — Orchestrator only
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${SA_ORCH}" --role="roles/storage.objectAdmin"

# ── 8b. Platform UI widening — a deliberate deviation from the isolation
#    above. mad_platform/web/app.py runs the *entire* pipeline (not just
#    publish-and-hand-off) directly inside scan-onboarding, so
#    scan-onboarding-sa needs everything scan-orchestrator-sa has, not
#    just Firestore+publish. Revisit before final production use: the
#    long-term shape should keep onboarding publish-only and let
#    scan-orchestrator do the actual work via Pub/Sub, same as the rest of
#    this script assumes.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_ONBOARDING}" --role="roles/aiplatform.user"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${SA_ONBOARDING}" --role="roles/storage.objectAdmin"

# mad-ui-access-code — the ONLY thing standing between scan-onboarding's
# --allow-unauthenticated endpoint and someone using it as a free
# Gemini-calling, Playwright-fetching open relay.
# Create the secret's first version manually before running this (its
# value must never be committed):
#   openssl rand -hex 12 | gcloud secrets create mad-ui-access-code --data-file=-
gcloud secrets add-iam-policy-binding mad-ui-access-code \
  --member="serviceAccount:${SA_ONBOARDING}" --role="roles/secretmanager.secretAccessor"

# Cloud Build needs its own permissions to build/push the Platform UI image
# and deploy it — separate from the app's own service accounts above.
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${COMPUTE_SA}" --role="roles/logging.logWriter"
gcloud artifacts repositories create mad-platform \
  --repository-format=docker --location="$REGION" \
  --description="MAD Platform service images" \
  || echo "skipped (already exists): mad-platform repo"
gcloud artifacts repositories add-iam-policy-binding mad-platform \
  --location="$REGION" \
  --member="serviceAccount:${COMPUTE_SA}" --role="roles/artifactregistry.writer"

# ── 9. Invocation IAM — dedicated identities invoke, NOT the target's own
#    service account. Avoids the circular pattern of a service
#    authenticating a push subscription to itself.
gcloud run services add-iam-policy-binding scan-orchestrator --region="$REGION" \
  --member="serviceAccount:${SA_PUBSUB_INVOKER}" --role="roles/run.invoker"
gcloud run services add-iam-policy-binding scan-wcag-poller --region="$REGION" \
  --member="serviceAccount:${SA_SCHEDULER_INVOKER}" --role="roles/run.invoker"

# ── 10. Pub/Sub push subscription, with dead-letter wired correctly ───────
ORCH_URL=$(gcloud run services describe scan-orchestrator --region="$REGION" --format='value(status.url)')

gcloud pubsub subscriptions create review-cycle-requests-sub \
  --topic=review-cycle-requests \
  --push-endpoint="$ORCH_URL" \
  --push-auth-service-account="$SA_PUBSUB_INVOKER" \
  --dead-letter-topic=review-cycle-requests-dlq \
  --max-delivery-attempts=5 \
  && echo "created subscription: review-cycle-requests-sub" \
  || echo "skipped (already exists): review-cycle-requests-sub"

# Dead-lettering needs Pub/Sub's own service agent (not any of our service
# accounts) to have publish rights on the DLQ and subscriber rights on the
# original subscription — this specific grant is easy to miss and silently
# breaks dead-lettering if skipped.
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
PUBSUB_SERVICE_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

gcloud pubsub topics add-iam-policy-binding review-cycle-requests-dlq \
  --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" --role="roles/pubsub.publisher"
gcloud pubsub subscriptions add-iam-policy-binding review-cycle-requests-sub \
  --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" --role="roles/pubsub.subscriber"

# ── 11. Cloud Scheduler — drives the WCAG poll tick ────────────────────────
WCAG_URL=$(gcloud run services describe scan-wcag-poller --region="$REGION" --format='value(status.url)')

gcloud scheduler jobs create http scan-wcag-poller-tick \
  --location="$REGION" \
  --schedule="0 */6 * * *" \
  --uri="$WCAG_URL" \
  --http-method=POST \
  --oidc-service-account-email="$SA_SCHEDULER_INVOKER" \
  && echo "created scheduler job: scan-wcag-poller-tick" \
  || echo "skipped (already exists): scan-wcag-poller-tick"

echo "Scaffolding complete. scan-onboarding is running the real Platform UI"
echo "image (see below) — the other three services are still on the"
echo "placeholder image, redeploy each once their code exists:"
echo '  gcloud builds submit --tag=us-central1-docker.pkg.dev/'"$PROJECT_ID"'/mad-platform/SERVICE:latest .'
echo '  gcloud run deploy SERVICE --image=us-central1-docker.pkg.dev/'"$PROJECT_ID"'/mad-platform/SERVICE:latest --region='"$REGION"
echo ""
echo "scan-onboarding's actual deploy command (mad_platform/web/app.py, from repo root):"
echo '  gcloud builds submit --tag=us-central1-docker.pkg.dev/'"$PROJECT_ID"'/mad-platform/scan-onboarding:latest --region='"$REGION"' .'
echo '  gcloud run deploy scan-onboarding \'
echo '    --image=us-central1-docker.pkg.dev/'"$PROJECT_ID"'/mad-platform/scan-onboarding:latest \'
echo '    --region='"$REGION"' --service-account='"$SA_ONBOARDING"' \'
echo '    --no-cpu-throttling --memory=1Gi --concurrency=4 --max-instances=3 --min-instances=0 \'
echo '    --set-secrets=MAD_ACCESS_CODE=mad-ui-access-code:latest --allow-unauthenticated'
