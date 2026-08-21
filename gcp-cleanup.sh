#!/usr/bin/env bash
# Cleanup for the tangled partial-Terraform-apply state — run this BEFORE
# gcp-deploy.sh. Deletes the Cloud Run services, Pub/Sub topics/subscriptions,
# the eight dedicated (non-generic) service accounts, and the duplicate
# "-9747" secrets. Leaves untouched: Firestore (scan-firestore), the bucket
# (scan-storage-9747), the two non-suffixed secrets, and the generic
# mad-platform-us-c1/c2 + default compute service accounts (not ours to
# manage — those belong to the Cloud Assist scaffolding itself).

REGION="us-central1"
PROJECT_ID="your-project-id-here"   # <-- EDIT THIS, must match gcp-deploy.sh

gcloud config set project "$PROJECT_ID"

# Note: no `set -e` in this file on purpose — this is a cleanup script, and
# a resource that's already gone (or never matched the exact name guessed
# from the earlier inventory) should be skipped, not treated as fatal.
# Each delete below reports what happened either way.

# ── Pub/Sub subscriptions (must delete before their topics) ───────────────
for sub in \
  scan-onboarding-9747 \
  scan-onboarding-9747-a5f851e85e5600cb54f3fecde322fc50 \
  scan-github-trigger-9747 \
  scan-github-trigger-9747-a5f851e85e5600cb54f3fecde322fc50 \
  scan-wcag-poller-9747 \
  scan-wcag-poller-9747-a5f851e85e5600cb54f3fecde322fc50 \
  scan-orchestrator-9747 \
  scan-orchestrator-9747-a5f851e85e5600cb54f3fecde322fc50
do
  gcloud pubsub subscriptions delete "$sub" --quiet \
    && echo "deleted subscription: $sub" \
    || echo "skipped (not found): $sub"
done

# ── Pub/Sub topics ──────────────────────────────────────────────────────────
for topic in review-cycle-requests review-cycle-requests-9747; do
  gcloud pubsub topics delete "$topic" --quiet \
    && echo "deleted topic: $topic" \
    || echo "skipped (not found): $topic"
done

# ── Cloud Run services ──────────────────────────────────────────────────────
for svc in scan-onboarding-9747 scan-github-trigger-9747 scan-wcag-poller-9747 scan-orchestrator-9747; do
  gcloud run services delete "$svc" --region="$REGION" --quiet \
    && echo "deleted service: $svc" \
    || echo "skipped (not found): $svc"
done

# ── The eight dedicated service accounts (both naming generations) ────────
# NOT touching mad-platform-us-c1/c2 or the default compute SA — leave those.
for sa in \
  scan-onboarding-9747-us-cen-sa \
  scan-onboarding-us-central1-sa \
  scan-github-trigger-9747-us-sa \
  scan-github-trigger-us-cent-sa \
  scan-wcag-poller-9747-us-ce-sa \
  scan-wcag-poller-us-central-sa \
  scan-orchestrator-9747-us-c-sa \
  scan-orchestrator-us-centra-sa
do
  gcloud iam service-accounts delete "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" --quiet \
    && echo "deleted service account: $sa" \
    || echo "skipped (not found): $sa"
done

# ── Duplicate "-9747" secrets — keep the non-suffixed ones ─────────────────
for secret in github-webhook-secret-9747 jira-email-secrets-9747; do
  gcloud secrets delete "$secret" --quiet \
    && echo "deleted secret: $secret" \
    || echo "skipped (not found): $secret"
done

echo "Cleanup done. Remaining: Firestore (scan-firestore), bucket (scan-storage-9747),"
echo "secrets (github-webhook-secret, jira-email-secrets), and the two generic"
echo "mad-platform-us-c* accounts. Run gcp-deploy.sh next."
