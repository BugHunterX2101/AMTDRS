# Deploying the hosted demo — Render.com (free tier)

Render's free web-service tier needs no credit card. This is the two-minute,
click-through path using the `render.yaml` Blueprint already in the repo root.

## Why Render, and what was tried first

Two other genuinely free paths were tried and ruled out for this specific
account, for the record:

- **Google Cloud Run** — the account's only billing account is closed and no
  project has billing enabled. Cloud Run requires an active billing account
  even for free-tier usage; it costs nothing to run this app there, but the
  account itself needs a payment method attached first.
- **Hugging Face Spaces** — as of this writing, HF requires a PRO subscription
  to host a Docker or Gradio Space on free `cpu-basic` hardware. Only static
  (no-backend) Spaces are free, which cannot run a FastAPI process.

Render's free web-service tier has no such gate: it builds directly from a
Dockerfile in a public GitHub repo, no card required. The trade-off is a cold
start after 15 minutes of inactivity (the container spins down and the first
request after that takes ~30–60s) — acceptable for a hackathon demo, worth
knowing about before a live judging call.

## Steps

1. **Sign up / log in** at [render.com](https://dashboard.render.com) — GitHub
   OAuth is the fastest path and is what step 2 needs anyway.

2. **New → Blueprint.** Render reads `render.yaml` from the repo root and
   proposes the service automatically — no manual field-filling needed.

3. **Connect the repository.** Authorize Render's GitHub App and select
   `BugHunterX2101/AMTDRS`. If the repo isn't listed, click "Configure account"
   and grant access to it specifically.

4. **Apply.** Render builds the `Dockerfile` in this repo (the identical image
   verified locally: `docker build -t principal . && docker run -p 8000:8000
   principal`) and deploys it. First build takes roughly 3–5 minutes.

5. **Verify.** Once live, `https://<service-name>.onrender.com/healthz` should
   return:
   ```json
   {"status":"ok","sandbox":"ContreeSandbox","inference_verified":true,"sandbox_verified":true,"live":true, ...}
   ```
   and `https://<service-name>.onrender.com/` serves the dashboard.

## Credentials: required, and the service never uses the stand-in

This service does not run the in-process `FakeSandbox`. `render.yaml` sets
`PRINCIPAL_REQUIRE_LIVE=1`, which makes startup fail unless Token Factory
inference and Nebius Sandboxes both verify, so a missing or wrong credential
fails the deploy instead of serving results that are not a platform
measurement. (Render is documented to keep the previous version running when a
deploy fails its health check; that was not exercised here.)

`render.yaml` declares the two required secrets with `sync: false` so their
values stay out of the repository. Set them in the Render dashboard,
**Environment** tab, before the first deploy:

| Key | Value |
|---|---|
| `NEBIUS_API_KEY` | your Token Factory inference key (required) |
| `NEBIUS_PROJECT_ID` | your project id; Sandboxes are authorised per project (required) |
| `CONTREE_TOKEN` | only if Sandboxes uses a separate token from inference |
| `GITHUB_TOKEN` | fine-grained, scoped to a fork you own, to enable draft PRs |
| `PRINCIPAL_FORK_REPO` | `owner/name` of that fork |

Mark all of these **Secret**. Setting variables through the Render API did
not start a deploy by itself, so trigger one (`POST /v1/services/<id>/deploys`)
or use **Manual Deploy** in the dashboard.

**A live service spends your quota.** The API has no authentication and no
per-visitor rate limit, and a request can set its own `token_budget`. Anyone who
has the URL can start jobs that use your Nebius tokens and sandbox time. Share
the URL deliberately.

## The service that is deployed today

`amtdrs.onrender.com` is a Render service named `amtdrs`. It was not created
from the Blueprint (the API reports no Blueprint for it), so `render.yaml` does
not configure it: its environment variables were set on the service directly.
The Blueprint is the way to create a new service of this kind.

The service keeps `principal.db` on the container's own disk, which the free
tier does not persist. Job ids from before a redeploy returned 404 afterwards
(observed 2026-10-26); the cause has not been confirmed with Render. Treat the
hosted history as temporary and keep a job's id and report if you need them.

## Redeploying after a push

The `amtdrs` service has auto-deploy on for `main`, so every push to `main` on
GitHub triggers a new deploy. Changing an environment variable needs a deploy
triggered by hand (see above).
