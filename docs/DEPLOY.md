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
   {"status":"ok","sandbox":"FakeSandbox","inference_configured":false, ...}
   ```
   and `https://<service-name>.onrender.com/` serves the dashboard.

## Optional: real Nebius credentials

The service runs out of the box with **zero credentials**, against the bundled
`tests/fixtures/mini_repo` fixture, via the in-process `FakeSandbox`. To switch
it to real Token Factory inference and Sandboxes:

In the Render dashboard, **Environment** tab, add:

| Key | Value |
|---|---|
| `NEBIUS_API_KEY` | your Token Factory inference key |
| `NEBIUS_PROJECT_ID` | your project id (Sandboxes are authorised per project) |
| `CONTREE_TOKEN` | only if Sandboxes uses a separate token from inference |
| `GITHUB_TOKEN` | fine-grained, scoped to a fork you own, to enable draft PRs |
| `PRINCIPAL_FORK_REPO` | `owner/name` of that fork |

Mark all of these **Secret**, not plain env vars. Redeploy after saving —
Render restarts the service automatically on an env var change.

## Redeploying after a push

`autoDeployTrigger: commit` in `render.yaml` means every push to `main` on
GitHub triggers a new deploy automatically. No manual step needed after the
first setup.
