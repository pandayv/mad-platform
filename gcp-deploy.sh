#!/usr/bin/env bash
# MAD Platform — GCP scaffolding via plain gcloud, replacing the Terraform
# attempt. Run this in Cloud Shell (console.cloud.google.com, top-right
# terminal icon) to avoid local auth/environment friction entirely.
#
# Run section by section, not necessarily all at once — read each comment
# before running that block. Placeholder container images are used for now;
# swap in real built images once the ADK agent code exists (§7 below).
#
# Run gcp-cleanup.sh FIRST if you had a prior partial `terraform apply` —
# this script assumes a clean slate for everything except Firestore, the
# bucket, and the two pre-existing secrets referenced below.

set -euo pipefail

PROJECT_ID="your-project-id-here"   # <-- EDIT THIS
REGION="us-central1"
PLACEHOLDER_IMAGE="us-docker.pkg.dev/cloudrun/container/hello"

# These two already exist from an earlier partial `terraform apply` —
# reusing them rather than fighting Terraform's leftover state. If you're
# starting genuinely fresh (no prior partial apply), these won't exist yet;
# uncomment the two `gcloud create` lines below and this block becomes a
# real bucket/database name of your choosing instead of a reused one.
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

# ── 4. Secret Manager — SKIPPED, already exist from the earlier partial
#    apply (kept by gcp-cleanup.sh): github-webhook-secret, jira-email-secrets
#    (one combined secret for both Jira and email creds, not split into two
#    — matching what already exists rather than adding a third pattern).
#    Add real values via `gcloud secrets versions add SECRET --data-file=-`
#    (reads stdin, keeps the value out of shell history) or the Console UI.
#    Don't put real secret values directly in this script or any file you commit.
echo "Using existing secrets: github-webhook-secret, jira-email-secrets"

# ── 5. Service accounts — one identity per service (what it can READ/WRITE),
#    plus two SEPARATE identities for who's allowed to INVOKE a service.
#    This is the exact distinction the Terraform got wrong — don't collapse
#    these back into one account each.
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
# boundary here, not Cloud Run IAM (per REQUIREMENTS.md §6.2).
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
# Firestore (all four need it, per the data model in REQUIREMENTS.md §8)
for sa in "$SA_ONBOARDING" "$SA_GITHUB" "$SA_WCAG" "$SA_ORCH"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${sa}" --role="roles/datastore.user"
done

# Pub/Sub publish — ONLY the three trigger services, and ONLY publisher,
# never subscriber (that was the core Terraform bug)
for sa in "$SA_ONBOARDING" "$SA_GITHUB" "$SA_WCAG"; do
  gcloud pubsub topics add-iam-policy-binding review-cycle-requests \
    --member="serviceAccount:${sa}" --role="roles/pubsub.publisher"
done

# Vertex AI — only the two services that actually call Gemini
for sa in "$SA_WCAG" "$SA_ORCH"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${sa}" --role="roles/aiplatform.user"
done

# Secrets — scoped to the SPECIFIC secret, not project-wide (the other gap
# from the Terraform review)
gcloud secrets add-iam-policy-binding github-webhook-secret \
  --member="serviceAccount:${SA_GITHUB}" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding jira-email-secrets \
  --member="serviceAccount:${SA_ORCH}" --role="roles/secretmanager.secretAccessor"

# Cloud Storage — Orchestrator only
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${SA_ORCH}" --role="roles/storage.objectAdmin"

# ── 9. Invocation IAM — dedicated identities invoke, NOT the target's own
#    service account. This is the fix for the circular pattern in the
#    Terraform (Orchestrator authenticating a push subscription as itself).
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

echo "Scaffolding complete. All four services are running placeholder images —"
echo "redeploy each with 'gcloud run deploy SERVICE --source .' once the ADK"
echo "agent code exists, from inside that service's own directory."
