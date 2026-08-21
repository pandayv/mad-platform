# MAD Platform — Setup Checklist

Working checklist to get from zero to "able to build." Grouped by
dependency order, not by category — do the urgent path first regardless of
what else looks appealing.

**Which Google property is which** (this trips people up):
- **Google Cloud Console** (`console.cloud.google.com`) — "GCP" itself:
  project, billing, Vertex AI, Cloud Run, Firestore, IAM. Almost everything
  below lives here.
- **Google AI Studio** (`aistudio.google.com`) — lighter-weight Gemini API
  key prototyping, not tied to a GCP project. Optional if going the Vertex
  AI route (recommended — see #10).
- **Google Developer Program** (`developers.google.com/program/gear`) —
  where GEAR training lives. Unrelated to the GCP project itself.
- **Devpost** (`allthingsagentichackathon.devpost.com`) — hackathon
  registration, credit form, resources, Discord, FAQ. Not a Google property.

---

## Urgent path (Aug 28, 2026 12pm PT credit deadline)

- [x] 1. Register on Devpost: https://allthingsagentichackathon.devpost.com/
- [x] 2. Create a dedicated GCP project: `project-d7e6174e-cca7-4d16-9d5`
- [x] 3. Billing enabled (confirmed working — resource creation succeeded)
- [~] 4. $150 GCP credit — **submitted**, pending review (Google noted a
      few days' turnaround). Non-blocking for now — the $300 new-account
      sign-up credit covers development in the meantime.
- [x] 5. Budget alerts set: **$50 per service** (4 separate budgets — one
      each for scan-onboarding, scan-github-trigger, scan-wcag-poller,
      scan-orchestrator), not one project-wide budget. Deliberately more
      targeted than a shared threshold — a runaway single service (e.g.
      Orchestrator over-calling Gemini) triggers its own alert rather than
      waiting for combined spend to cross one line.

## GCP project setup

- [x] 6. Required APIs enabled (Cloud Run, Firestore, Pub/Sub, Secret
      Manager, Cloud Storage, Vertex AI, Cloud Scheduler)
- [x] 7. gcloud CLI — via **Cloud Shell**, not a local install. Terraform
      (via Cloud Assist) proved unreliable after multiple rounds of
      incorrect IAM/Pub/Sub generation — see `gcp-cleanup.sh` and
      `gcp-deploy.sh` in this folder, which are the actual infra-as-code
      record now. Run in Cloud Shell, idempotent (safe to re-run), fully
      documents the real deployed architecture: 4 Cloud Run services, one
      Firestore database, one Pub/Sub topic + dead-lettered push
      subscription, 6 least-privilege service accounts, Cloud Scheduler.
      **Verified 2026-08-19**: `get-iam-policy` on all four services
      confirms exactly the intended access — `scan-onboarding` and
      `scan-github-trigger` public, `scan-wcag-poller` and
      `scan-orchestrator` reachable only by their dedicated invoker
      identities.
- [x] 8. Firestore database created: `scan-firestore` (Native mode)
- [x] 9. Least-privilege service accounts created (6, not 1 — see
      `REQUIREMENTS.md` §7.1 for why the split)
- [x] 10. Vertex AI (not Google AI Studio) — `aiplatform.googleapis.com`
      enabled, `roles/aiplatform.user` granted only to
      `scan-wcag-poller-sa` and `scan-orchestrator-sa`
- [x] 11. Confirmed via `client.models.list()`: only Flash-tier models meet
      "Gemini 3.5+" in this project — `gemini-3.5-flash`,
      `gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.7-flash`. No
      Pro-tier model exists at that version floor — see `REQUIREMENTS.md`
      §6.3 for the resulting tiering adjustment.

## Local dev environment

- [x] 12. ADK docs: https://google.github.io/adk-docs
- [x] 13. ADK source: https://github.com/google/adk-python
- [x] 14. **Hello-world agent confirmed working end-to-end (2026-08-21)**:
      `.venv` (Python 3.14, not 3.9 — see below), `adk create`, ran through
      the real `adk` CLI against Vertex AI, got a correct response back.
      Two real gotchas hit and fixed along the way, both apply to every
      future service, not just this one:
      - **Python 3.9 → 3.14**: started on system Python 3.9 (google-adk
        installs fine there), but `adk` itself warned "MCP requires Python
        3.10 or above" — a real capability gap, not cosmetic, plus constant
        google-auth/api_core EOL warnings on every command. Switched to
        Python 3.14 (via Homebrew) before writing any real code, since this
        underlies everything going forward. Project venv lives at `.venv/`
        in the repo root (gitignored).
      - **Region: `global`, not `us-central1`.** `gemini-3.5-flash-lite`
        appears in the Vertex AI model catalog listing regardless of
        location queried, but actually calling it in `us-central1` 404s —
        "Publisher model ... was not found ... in the specified region."
        `location='global'` works. **Every future service's Vertex AI
        client must use `global`, not `us-central1`**, despite Cloud Run
        itself being deployed in `us-central1` — these are independent
        settings, don't assume they need to match.
- [ ] 15. Docker Desktop: https://www.docker.com/products/docker-desktop/
- [ ] 16. Playwright Python: https://playwright.dev/python/docs/intro —
      `pip install playwright && playwright install chromium`
- [ ] 17. Create the public GitHub repo for MAD Platform now (commit history
      from today is part of the "newly created" evidence): https://github.com/new

## Third-party accounts

- [ ] 18. Jira Cloud (free tier): https://www.atlassian.com/software/jira/free
      — API tokens: https://id.atlassian.com/manage-profile/security/api-tokens
      — note the project key (open item in REQUIREMENTS.md §11)
- [ ] 19. Email sending — SendGrid free tier: https://sendgrid.com, or a
      dedicated Gmail account + app password for SMTP
- [ ] 20. Demo target site hosting — GitHub Pages: https://pages.github.com/
      or Netlify: https://www.netlify.com/ (build a small site with a few
      deliberate WCAG violations; this repo also doubles as the GitHub
      deploy-detection test target)

## Optional

- [ ] 21. GEAR / Google Developer Program (only if ADK ramp-up is needed):
      https://developers.google.com/program/gear

## Hackathon reference pages (verified)

- Resources: https://allthingsagentichackathon.devpost.com/resources
- FAQ: https://allthingsagentichackathon.devpost.com/details/faqs
- Discord: https://discord.gg/HP4BhW3hnp
