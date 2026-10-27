# Deploying to the EC2 box (t3.large, private VPC)

Stack: `db` + `migrate` + `api` + `worker` via Docker Compose. Push-to-deploy
through a self-hosted GitHub Actions runner. Secrets live only on the box.

Legend: 🖥️ = run on the EC2 box (SSH) · 💻 = run on your Mac · 🌐 = GitHub/GitLab UI.

---

## Phase A — Install Docker + the runner on the box

🖥️ Install Docker Engine + Compose:
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl rsync
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER   # log out/in after this so `docker` works without sudo
```
Verify (new shell): `docker --version && docker compose version`

🖥️ Pick a free API port (default 8642):
```bash
ss -ltnp | grep -E ':8642|:8000' || echo "8642 is free"
```

## Phase B — Source on GitHub + self-hosted runner

💻 Push the project to a **private** GitHub repo:
```bash
cd /Users/rishabhsingh/Desktop/MR_review_agent
git init && git add -A && git commit -m "MR review agent"
git branch -M main
git remote add origin git@github.com:<you>/mr-review-agent.git
git push -u origin main
```
(`.env`, `.venv`, `data/` are gitignored — no secrets are pushed.)

🌐 GitHub repo → **Settings → Actions → Runners → New self-hosted runner** →
follow the Linux x64 commands it shows, ON the box. Install it as a service:
```bash
sudo ./svc.sh install && sudo ./svc.sh start
```
The runner user must be in the `docker` group and able to write `/opt/mr-review-agent`:
```bash
sudo usermod -aG docker <runner-user>
sudo mkdir -p /opt/mr-review-agent && sudo chown <runner-user> /opt/mr-review-agent
```

## Phase C — Create the `.env` on the box (once)

🖥️
```bash
cd /opt/mr-review-agent
# create .env with real values:
cat > .env <<'EOF'
POSTGRES_USER=mragent
POSTGRES_PASSWORD=<strong-db-password>
POSTGRES_DB=mragent
API_PORT=8642
GITLAB_URL=https://<your-gitlab>
GITLAB_TOKEN=<glpat-... scope api>
GITLAB_SSL_VERIFY=true
WEBHOOK_SECRET=<strong-random-secret>
REVIEWER_ALLOWLIST=<user1,user2>
LLM_PROVIDER=openai_compat
OPENAI_API_KEY=<sk-...>
REVIEW_MODEL=gpt-4o
FAST_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=256
DAILY_BUDGET_USD=25
REVIEW_CONCURRENCY=4
EOF
chmod 600 .env
```
(`DATABASE_URL` is set by compose to point at the `db` container — don't add it here.)

## Phase D — First deploy

🌐 Push to `main` (or run the workflow manually). The runner builds and starts
everything. Or do the first one by hand to watch it:

🖥️
```bash
cd /opt/mr-review-agent && docker compose up -d --build && docker compose ps
```

## Phase E — Verify

🖥️
```bash
curl -s localhost:8642/health          # {"status":"ok"}
docker compose exec db psql -U mragent -d mragent -c "\dt"   # 10 tables
docker compose logs -f worker          # "Worker ... started"
```

## Phase F — Wire the GitLab webhook (automatic /review)

- Security group: allow the GitLab host/subnet → this box on **TCP 8642**.
- 🌐 GitLab project → **Settings → Webhooks → Add**:
  - URL: `http://<box-private-ip>:8642/webhooks/gitlab`
  - Secret token: **same as `WEBHOOK_SECRET`**
  - Triggers: **Comments** (Note events) — not "Merge request events" — and
    **Push events** (default-branch pushes keep the RAG index fresh; other
    branches are ignored)
  - (If GitLab blocks local-network hooks: Admin → Settings → Network →
    Outbound requests → allow requests to the local network.)
- Test: on an MR, an allowlisted user comments `/review`. Watch `docker compose logs -f worker`.

## Phase G — Grounding (after it works)

Index a repo so reviews aren't diff-only. Pulls the archive via the GitLab API —
no checkout needed (run inside the worker container):
```bash
docker compose exec worker python -m app.rag.index_gitlab_cli --project <gitlab_project_id>
```
After this first full index, default-branch pushes re-index incrementally.

## Adding another project

One deployment serves any number of GitLab projects — the project id comes from
the webhook payload and each project gets its own RAG index. Per project:

1. Give the bot (`GITLAB_TOKEN` user) **Developer** access to it.
2. Verify: `docker compose exec worker python -m app.gitlab.whoami_cli --project <id>`
3. Add the webhook (Phase F) on that project — same URL + secret.
4. Index it once (Phase G).

| Project | id |
|---|---|
| `mastersindia/mi_automation_suite` | 200 |

---

## Ops cheatsheet
```bash
docker compose ps                 # status
docker compose logs -f api        # api logs
docker compose logs -f worker     # worker logs
docker compose restart worker     # restart one service
docker compose down               # stop all (pgdata volume persists)
```
