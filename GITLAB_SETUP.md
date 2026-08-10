# GitLab Go-Live

Two stages: (1) prove connection + review + posting against a real MR manually,
(2) turn on the automatic `/review` webhook.

## Stage 1 — connect + review a real MR (outbound only)

### 1. Create a bot token

Use a dedicated bot user (recommended) or a project/group access token:

- **Project Access Token** (simplest): your project → **Settings → Access Tokens**
  - Role: **Developer** (needed to post comments; Maintainer if you also want it
    to *approve* MRs)
  - Scopes: **`api`**
  - Copy the token (starts with `glpat-`).

### 2. Fill `.env`

```bash
GITLAB_URL=https://gitlab.your-company.internal
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
GITLAB_SSL_VERIFY=true          # false (or a CA path) for self-signed internal certs
```

### 3. Verify connectivity (from `backend/`)

```bash
python -m app.gitlab.whoami_cli
python -m app.gitlab.whoami_cli --project <PROJECT_ID>
```

Expect `✅ Authenticated as: <bot>` and `✅ Project access: ...`.

### 4. Dry-run a real MR (fetch + review, nothing posted)

```bash
python -m app.orchestrator.review_mr_cli --project <PROJECT_ID> --mr <MR_IID>
```

### 5. Post for real

```bash
python -m app.orchestrator.review_mr_cli --project <PROJECT_ID> --mr <MR_IID> --post
```

Inline comments post only if the gate is `auto_post`; `escalate`/`hitl` reviews
are persisted and wait in `GET /hitl/pending`.

## Stage 2 — automatic `/review` webhook

Requires the API to be reachable **from** the GitLab server.

1. Run the API where GitLab can reach it (EC2 box in-network, or a tunnel for
   local testing: `cloudflared tunnel --url http://localhost:8000`).
2. Set `WEBHOOK_SECRET` and `REVIEWER_ALLOWLIST` in `.env`.
3. GitLab project → **Settings → Webhooks → Add**:
   - URL: `https://<your-endpoint>/webhooks/gitlab`
   - Secret token: same as `WEBHOOK_SECRET`
   - Trigger: **Comments** (this is the Note event — NOT "Merge request events")
4. Start the worker: `python -m app.worker.worker`
5. On any MR, an allowlisted user comments `/review` (or `/review security`).
