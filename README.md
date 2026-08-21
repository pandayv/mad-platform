# MAD Platform

**An autonomous agent that scans a website for accessibility problems,
verifies its own findings, and takes real action on what's confirmed — not
just a report.**

Built for the [All Things Agentic Hackathon](https://allthingsagentichackathon.devpost.com/)
on Gemini, Google's Agent Development Kit (ADK), and Google Cloud.

---

## Try it live

- **Scan a site:** [MAD Platform Web App](https://scan-onboarding-803013053073.us-central1.run.app)
  — the public form is access-gated (a real security measure, not a demo
  limitation — see [Known gaps](#known-gaps-and-whats-next) below for why).
  See it in action in the demo video, or reach out for a live walkthrough.
- **A site to try it against:** [Guardian Pest Control](https://pandayv.github.io/mad-platform/)
  — a small fictional business site with deliberate accessibility
  violations, built as a consistent, reproducible scan target (see
  [`docs/`](docs/)).
- **Architecture diagram:** [MAD Platform Architecture](https://claude.ai/code/artifact/a248d861-2f0d-4176-801e-af7c748a9309)
  — the Review Cycle pipeline, the WCAG auto-heal loop, and the Google
  Cloud infrastructure behind it.

## The problem

Website-accessibility lawsuits (ADA-related, in the US) are a real and
growing risk for small businesses, most of whom have no practical way to
know they're exposed. Manual accessibility audits are expensive and slow.
Automated scanners exist, but they're noisy — full of false positives a
non-technical business owner can't triage — and a report alone doesn't fix
anything; someone still has to turn it into work that gets done.

MAD Platform removes that blind spot: point it at a URL, and it finds real
issues, checks its own work before trusting it, explains what matters most
in plain language, and files the confirmed ones as tickets automatically —
while routing the genuinely uncertain ones to a human instead of guessing.

## What it does

1. **Scans a site autonomously** — decides which pages matter most on its
   own (home, contact, forms), then checks them with both deterministic
   rule checks (contrast, missing alt text, heading structure, form
   labels, ARIA misuse, tab order) and AI-assisted review for what rules
   can't judge, like whether alt text is actually descriptive.
2. **Verifies its own findings** — every flag is independently
   double-checked before it's trusted; false positives get dismissed with
   a documented reason, real findings get a confidence score.
3. **Ranks by real-world risk** — not raw technical severity: WCAG
   conformance level, how often that violation type shows up in real
   accessibility litigation, and estimated user impact.
4. **Produces an actionable report** — a styled, self-contained HTML
   report with an overall score, severity breakdown, plain-English
   executive summary, and a concrete suggested fix per finding.
5. **Takes real action** — files a ticket automatically for every
   confirmed finding; routes the low-confidence or critical minority to a
   human reviewer instead, who can confirm or dismiss.
6. **Recovers from failure** — a scan interrupted mid-way (crash, redeploy)
   resumes from its last completed checkpoint rather than starting over or
   silently duplicating work.
7. **Keeps its own reference material current** — periodically checks
   whether the WCAG standard itself has changed, auto-refreshing for minor
   additive updates and routing structural changes to human review before
   acting on them.

## Try it yourself: what a scan looks like

Paste a URL into the web app, and watch it work:

![Scan in progress, with live phase labels and per-page checklist](assets/screenshot-progress.png)

When it's done, you get a score, a severity breakdown, and the full report:

![Completed scan result](assets/screenshot-completed.png)

## How it works

MAD Platform is deliberately built around four things Google's own ADK
webinar series named as what's being judged — not as a checklist to
satisfy, but because they're genuinely the right engineering choices for
this problem:

| Lens | Where it shows up |
|---|---|
| **Orchestration patterns** | Sequential pipeline for ordered stages (crawl → analyze → verify → rank → act → report); parallel fan-out for independent per-page checks; dynamic/LLM-driven delegation only at real judgment points (which pages to scan, whether to retry) |
| **Resumability** | Every stage checkpoints its completion to Firestore as it finishes; a restart resumes from the last completed stage, never blindly from the top |
| **Feedback loops** | A bounded retry gate (Orchestrator decides "good enough, or one more pass?" — capped at one, not an open loop) and the WCAG auto-heal loop (detects a version change, classifies it, auto-acts or escalates) |
| **Memory / vector search** | WCAG success criteria are embedded once and retrieved to ground every citation, preventing hallucinated success-criterion numbers; the auto-heal loop keeps that memory current instead of letting it go stale |

Every irreversible action (filing a ticket, refreshing the knowledge base)
is either idempotent, gated behind human approval, or both — a retried
pipeline step never double-files, and a structural change to the
accessibility standard itself never gets auto-applied without a person
looking at it first.

## Product layer vs. platform layer

This build deliberately separates two things:

- **Product layer (built):** a place to come check your website's
  accessibility and get an actionable report — one-time, on-demand,
  no registration required. Everything above is this layer.
- **Platform layer (intentionally deferred):** registering a site for
  *ongoing* monitoring, detecting when a registered site's code changes
  (GitHub webhook), and the recurring scheduling that ties them together.
  Real, designed, and partially infra-provisioned, but scoped out of this
  build so the product layer could be hardened first rather than spread
  thin across both.

## Tech stack

- **AI:** Gemini via Vertex AI (`gemini-3.5-flash-lite` for high-volume
  calls, `gemini-3.7-flash` for judgment calls — no Pro-tier model exists
  at the "Gemini 3.5+" floor this hackathon requires, confirmed directly
  against the model catalog)
- **Agent framework:** Google Agent Development Kit (ADK)
- **Compute:** Cloud Run (scale-to-zero), four services split by trigger
  type and resource profile
- **State:** Firestore — job checkpoints, findings, escalation queue,
  WCAG knowledge-base embeddings
- **Storage:** Cloud Storage — generated reports
- **Scheduling:** Cloud Scheduler — drives the WCAG freshness check
- **Browser automation:** Playwright — headless rendering, screenshots,
  computed-style extraction for real contrast-ratio checking
- **Web:** FastAPI — the scan-submission UI and status API
- **Ticketing:** Jira REST API, behind an abstraction (`IssueSink`) so a
  second tracker could be added without touching Orchestrator or Reporter

## Known gaps and what's next

Disclosed deliberately, not discovered by a judge:

- **Real Jira credentials aren't wired up yet** — tickets file against a
  mock sink today; the abstraction is real, the credentials are the
  remaining step.
- **The pipeline calls Gemini via the raw SDK, not ADK's `Agent`/`Runner`
  constructs** — a hello-world agent in this repo proves the ADK toolchain
  works end to end; formalizing the real pipeline onto it is a real
  robustness item, not done yet.
- **`robots.txt` isn't respected yet** — a stated requirement, not yet
  implemented. Part of why the demo site is one we own rather than
  leaning on repeated scans of real third-party businesses.
- **The web app's service account is broader than the original
  least-privilege design** — it runs the whole pipeline directly rather
  than publishing an event for a separate worker to pick up, so it holds
  more access than that split intended. A disclosed, deliberate
  simplification to ship a working demo, tracked to revisit.
- **Platform layer** (site registration, recurring monitoring, GitHub
  webhook) — see above; a real next phase, not core to this submission.

## Local setup

```bash
git clone https://github.com/pandayv/mad-platform.git
cd mad-platform
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium

# Requires a GCP project with Vertex AI, Firestore, and Cloud Storage
# enabled, and application-default credentials configured:
gcloud auth application-default login

# Run a scan from the CLI (no frontend needed for this):
python run_scan.py https://example.com

# Or run the web app locally:
uvicorn mad_platform.web.app:app --port 8080
```

See [`SETUP.md`](SETUP.md) for the full GCP provisioning checklist and
[`gcp-deploy.sh`](gcp-deploy.sh) for the actual infrastructure-as-code used
to deploy this project's Cloud Run services, Firestore database, Pub/Sub
topics, and service accounts.

## Project structure

```
mad_platform/
  agents/        # Orchestrator, Analyst, Editor, Reporter, Action Agent, WCAG auto-heal
  tools/         # Crawler, rule checks, AI checks, Gemini client, RAG, WCAG version fetch
  state/         # Firestore + Cloud Storage clients
  web/           # The scan-submission UI and the WCAG-poller HTTP entrypoint
  data/          # Curated WCAG success-criteria corpus
docs/            # Demo site (GitHub Pages)
run_scan.py               # CLI entry point for a one-time scan
review_escalations.py     # SME review queue CLI
check_wcag_version.py     # Manual/demo trigger for the WCAG freshness check
SETUP.md                  # GCP provisioning checklist
gcp-deploy.sh / gcp-cleanup.sh   # Infrastructure-as-code
```

## Built during the hackathon submission window

Solo build by Vipul Panday, drawing on a professional background in risk
management and compliance.
