# MR Review Agent

Production-grade AI Merge Request review agent for **self-hosted GitLab**.

On-demand: an allowlisted reviewer comments `/review` on an MR → the agent fans out to
specialist reasoners (security / quality / tests / docs) grounded in full-repo RAG, then
posts inline findings back to the MR. Backed by one Postgres (pgvector + pgvectorscale +
TimescaleDB) for memory, truth, and time-series audit/cost.

See the full design in the plan file referenced during development.

## Status — building incrementally

- [x] **Bit 1** — Foundation: project skeleton, config, Postgres with pgvector + pgvectorscale + TimescaleDB
- [x] **Bit 2** — DB schema (SQLAlchemy models + Alembic migration: memory / truth / time / jobs)
- [x] **Bit 3** — GitLab I/O layer (client + diff/position mapping + publisher)
- [x] **Bit 4** — Webhook ingress + Postgres job queue/worker
- [x] **Bit 5** — LLM provider layer (Anthropic + OpenAI-compatible + embeddings + cost)
- [x] **Bit 6** — RAG memory (chunk + embed + hybrid retrieve)
- [x] **Bit 7** — Specialist agents + aggregator/gate + LangGraph orchestrator
- [x] **Bit 8** — Live worker integration (GitLab → graph → publish → persist) + dry-run CLI
- [x] **Bit 9** — Telemetry (agent_events) + cost ledger + budget guard
- [x] **Bit 10** — HITL approval API + review/trace/cost read endpoints
- [x] **Bit 11** — Reliability (retries/backoff + agent timeouts) + eval harness
- [x] **Bit 12** — Dashboard (frontend): zero-build static UI served by FastAPI

## Local setup (Bit 1)

Prerequisites: Docker + Docker Compose, Python 3.12.

```bash
# 1. Configure
cp .env.example .env      # edit values as needed

# 2. Bring up the database
docker compose up -d db

# 3. Verify extensions are installed
docker compose exec db psql -U mragent -d mragent -c "\dx"
```

You should see `timescaledb`, `vector`, and `vectorscale` listed.

## Bit 2 — Apply the database schema

Needs Python 3.12. From the project root:

```bash
# 1. Create + activate a virtualenv, install the package
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
# 2. Run the migration (creates extensions, all tables, indexes, hypertable)
cd backend
alembic upgrade head
```

```bash
# 3. Verify the tables exist
docker compose exec db psql -U mragent -d mragent -c "\dt"
```

Expected tables: `repositories`, `webhook_deliveries`, `mr_review_records`,
`finding_records`, `hitl_reviews`, `hitl_feedback`, `code_chunks`, `jobs`,
`agent_events`. Confirm the hypertable:

```bash
docker compose exec db psql -U mragent -d mragent \
  -c "SELECT hypertable_name FROM timescaledb_information.hypertables;"
```

Should return `agent_events`.

## Bit 3 — GitLab I/O layer

Client (`app/gitlab/client.py`), diff parsing + inline-position mapping
(`app/gitlab/diff.py`), and the MR publisher (`app/gitlab/publish.py`). The diff
logic is unit-tested without needing a GitLab connection:

```bash
# from the project root, venv active
pytest backend/tests/test_diff.py -v
```

All tests should pass. Actual GitLab connectivity is exercised in Bit 4 (webhook
ingress) once the trigger path is wired.

## Bit 4 — Webhook ingress + job queue/worker

The full trigger path: `/review` comment → validate token → allowlist → parse
command → dedupe → enqueue → worker picks it up. Verifiable locally with no real
GitLab (we POST a sample payload).

Prereqs: `db` container up, migration applied, `.env` has
`WEBHOOK_SECRET=another-strong-random-secret` and `REVIEWER_ALLOWLIST` includes
`alice` (defaults from `.env.example` already satisfy this).

Unit tests first:

```bash
pytest backend/tests/test_webhook_parser.py -v
```

Then the live loop — use **three terminals** (all with the venv active):

```bash
# Terminal 1 — API
cd backend && uvicorn app.main:app --reload --port 8000
```

```bash
# Terminal 2 — worker
cd backend && python -m app.worker.worker
```

```bash
# Terminal 3 — send a sample /review webhook
curl -s -X POST http://localhost:8000/webhooks/gitlab \
  -H "X-Gitlab-Token: another-strong-random-secret" \
  -H "X-Gitlab-Event: Note Hook" \
  -H "Content-Type: application/json" \
  --data @samples/note_webhook.json
```

Expected:
- curl returns `{"status":"queued","job_id":...,"scope":"security"}`
- Terminal 2 (worker) logs `REVIEW job received: project=42 MR=!7 scope=security ...` then `Job N (review_mr) done`
- A second identical curl returns `{"status":"duplicate"}` (idempotency on note id)
- A wrong token returns HTTP 401; a non-allowlisted user returns `{"status":"unauthorized"}`

Inspect the queue directly:

```bash
docker compose exec db psql -U mragent -d mragent -c "SELECT id, job_type, status, attempts FROM jobs ORDER BY id;"
```

## Bit 5 — LLM provider layer

Provider-abstracted (`app/llm/`): `AnthropicProvider` + `OpenAICompatProvider`
(covers OpenAI and local vLLM/Ollama) behind one `LLMProvider` interface, an
`Embedder` (OpenAI, 256-dim), a cost estimator (`pricing.py`), and a factory
with per-agent model routing. Providers return token counts + USD cost on every
call. Tested with fake SDK clients — **no API keys or network needed**:

```bash
pytest backend/tests/test_llm.py -v
```

Real keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) and exact model IDs
(`REVIEW_MODEL`, `FAST_MODEL`) are only needed when we run live reviews.

## Bit 6 — RAG memory

Chunk repo code (`chunker.py`), embed + store in `code_chunks` (`indexer.py`),
and hybrid-retrieve via vector DiskANN + full-text with reciprocal rank fusion
(`retriever.py`).

Pure unit tests (chunking + fusion, no network):

```bash
pytest backend/tests/test_rag.py -v
```

Live end-to-end (needs `OPENAI_API_KEY` in `.env` and the `db` container up).
Index a small folder, then search it:

```bash
cd backend
python -m app.rag.index_cli ./app --name local/sample
```

```bash
python -m app.rag.search_cli "how are webhook tokens verified"
```

The index step prints files/chunks/tokens/cost; the search step prints the most
relevant code chunks (file:line + snippet). Confirm the rows landed:

```bash
docker compose exec db psql -U mragent -d mragent -c "SELECT count(*) FROM code_chunks;"
```

## Bit 7 — Specialist agents + orchestrator

Four specialists (security/quality/tests/docs) return structured `Finding`s
(`agents/`), an aggregator dedups them and a confidence gate routes the review
(auto-post / HITL / escalate), all wired in a LangGraph pipeline
(`orchestrator/graph.py`: retrieve → fan-out → aggregate → gate).

Fully tested with a fake provider — **no network/API key**:

```bash
pytest backend/tests/test_agents.py -v
```

Next bit wires this graph into the worker for a live review (GitLab fetch →
graph → post comments → persist), which is when real LLM calls happen.

## Bit 8 — Live review + dry-run

The worker's `review_mr` handler now runs a real review
(`orchestrator/service.py`): fetch MR → run graph → auto-post inline+summary (if
the gate allows) → persist review + findings. GitLab isn't required yet — a
**dry-run CLI** runs the real LLM review over a saved change set and prints what
it *would* post.

Pure tests (no network):

```bash
pytest backend/tests/test_orchestration.py -v
```

Live dry-run (uses `OPENAI_API_KEY`; grounds on the `local/sample` index if you
ran Bit 6). From `backend/`:

```bash
python -m app.orchestrator.review_cli ../samples/mr_changes.json --scope all
```

The sample diff adds a hardcoded password + an f-string SQL query, so expect the
security agent to flag CRITICAL findings and the gate to route to `escalate`.
No comment is posted anywhere; the DB transaction is rolled back.

**Going live (later):** once a self-hosted GitLab project + bot token are set in
`.env`, the same flow runs automatically from a real `/review` webhook — no code
change.

## Bit 9 — Telemetry, cost ledger, budget guard

Every LLM call + each review outcome is written to the `agent_events` hypertable
(`telemetry/events.py`); `telemetry/cost.py` sums today's spend and blocks
reviews over `DAILY_BUDGET_USD`. Per-agent latency is captured too.

Pure tests:

```bash
pytest backend/tests/test_telemetry.py -v
```

Live: run a review and PERSIST it + telemetry (uses `OPENAI_API_KEY`, db up),
then inspect the ledger. From `backend/`:

```bash
python -m app.orchestrator.review_cli ../samples/mr_changes.json --record
```

```bash
python -m app.telemetry.cost_cli
```

Confirm events landed and see the cost breakdown:

```bash
docker compose exec db psql -U mragent -d mragent \
  -c "SELECT event_type, agent_type, model, tokens_in, tokens_out, cost_usd, latency_ms, outcome FROM agent_events ORDER BY ts;"
```

You'll see one `llm_call` row per specialist plus one `review` row, and
`cost_cli` prints today's spend vs the budget.

## Bit 10 — HITL approval API + read endpoints

FastAPI endpoints (`api/reviews.py`, `api/hitl.py`) over the persisted data:
reviews list/detail, per-review event trace, today's cost, the pending HITL
queue, and approve/reject/dispute actions.

Pure test:

```bash
pytest backend/tests/test_orchestration.py -v
```

Live (needs the `--record` review from Bit 9 in the DB, and the API running):

```bash
cd backend && uvicorn app.main:app --port 8000
```

In another terminal:

```bash
curl -s localhost:8000/reviews | python -m json.tool
curl -s localhost:8000/reviews/1 | python -m json.tool
curl -s localhost:8000/reviews/1/events | python -m json.tool
curl -s localhost:8000/hitl/pending | python -m json.tool
curl -s localhost:8000/cost/today | python -m json.tool
```

Reject / dispute work without GitLab:

```bash
curl -s -X POST localhost:8000/hitl/1/reject -H 'Content-Type: application/json' -d '{"decided_by":"you","reason":"false positive"}'
```

`POST /hitl/{id}/approve` posts the held findings to the real MR — so it needs a
configured GitLab (`GITLAB_URL`/`GITLAB_TOKEN`) and a matching project/MR.

## Bit 11 — Reliability + eval harness

Transient LLM/embedding/network failures are retried with backoff
(`reliability.py`, applied to both providers + the embedder); each specialist
call is bounded by `AGENT_TIMEOUT_SECONDS`. An eval harness scores the reviewer
against labeled cases.

Pure tests (retry behavior):

```bash
pytest backend/tests/test_reliability.py -v
```

Run the evals (real LLM calls; db up, `OPENAI_API_KEY` set). From `backend/`:

```bash
python -m app.evals.runner
```

Prints per-case recall and 'extra' findings, then totals. Expect the SQLi case
to match both labels and the clean case (scope=security) to ideally show 0
extras (false positives). Add cases under `app/evals/cases/*.json` to expand
coverage and tune `CONFIDENCE_THRESHOLD`.

## Bit 12 — Dashboard

A dependency-free single-page UI (`app/static/`: `index.html` + `styles.css` +
`app.js`, no Node/build step) served same-origin by the API, so it calls the
Bit 10 read/HITL endpoints directly (no CORS). Views: **Overview** (KPIs +
recent reviews), **Reviews** (list → detail drawer with findings & the per-agent
trace), **HITL Queue** (approve / reject / dispute), and **Cost** (spend vs
`DAILY_BUDGET_USD`). Light/dark theme included.

Start the API and open the dashboard:

```bash
cd backend && uvicorn app.main:app --port 8000
```

Then visit <http://localhost:8000/dashboard/>. With a `--record` review in the
DB (Bit 9), the reviews, trace, HITL queue, and cost panels populate from live
data.

## Review a REAL local git diff (no GitLab needed)

Point the reviewer at any git repo on your machine (`db` up, `OPENAI_API_KEY`
set). From `backend/`:

```bash
# review the last few commits of a real repo
python -m app.orchestrator.review_git_cli /path/to/some/repo --base HEAD~3

# focus one specialist
python -m app.orchestrator.review_git_cli /path/to/some/repo --base HEAD~3 --scope security

# ground the review on that repo's code (makes embedding calls first)
python -m app.orchestrator.review_git_cli /path/to/some/repo --base HEAD~5 --index
```

Dry run — nothing is posted or persisted. This is the recommended way to gauge
real-world review quality before wiring up GitLab.
