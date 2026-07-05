# Production deployment

**DevOps handoff:** **[docs/DEVOPS_DEPLOY.md](DEVOPS_DEPLOY.md)** — single guide for EC2 API, widget, verify, and re-ingest.

## EC2 (single instance)

Deploy API + Postgres + Redis on one EC2 instance with Docker Compose. CI/CD: **`.github/workflows/deploy-ec2.yml`**.

Detail: **[docs/EC2.md](EC2.md)** — instance setup, GitHub secrets, HTTPS.

Environment template: **`.env.devops.example`** → `.env.production` on the server (`.env.ec2.example` is equivalent).

---

## AWS ECS Fargate (managed stack)

Deploy with Terraform: **[docs/AWS.md](AWS.md)** and **[deploy/terraform/README.md](../deploy/terraform/README.md)**

```bash
cd deploy/terraform && terraform init && terraform apply
```

GitHub Actions: `.github/workflows/deploy-aws.yml` (requires Terraform outputs as GitHub secrets).

Environment template: `.env.aws.example`

---

## API (Cloud Run — alternative)

```bash
# Build and deploy (example)
gcloud run deploy mobcoder-assistant \
  --source . \
  --region us-central1 \
  --set-env-vars "APP_ENV=production,ENABLE_DOCS=false,ENABLE_SOURCE_ENDPOINTS=false,ENABLE_DEBUG_ENDPOINTS=false,OPENAI_API_KEY=...,CORS_ALLOWED_ORIGINS=https://mobcoder.ai,https://www.mobcoder.ai" \
  --set-secrets "APIFY_API_TOKEN=apify-token:latest,HUBSPOT_WEBHOOK_URL=hubspot-url:latest"
```

Local parity:

```bash
docker compose up --build
```

Production-like stack (Redis + API + nginx widget proxy):

```bash
cp .env.production.example .env.production
# Edit .env.production — set OPENAI_API_KEY
docker compose -f docker-compose.prod.yml --env-file .env.production up --build -d
```

- API health: `http://127.0.0.1:8001/api/v1/health`
- Widget demo: `http://127.0.0.1:8765/demo.html`

On first boot the entrypoint runs `seed_knowledge.py --ingest` when Chroma is empty (`AUTO_INGEST_ON_START=true`).

Health: `GET /api/v1/health` returns `chunks`, `last_ingest_at`, `vector_store_count`.

## Chroma persistence

- Persist `./data/embeddings/chroma` on a volume or rebuild from `data/chunks/chunks_*.json` after deploy.
- Restore: copy volume snapshot **or** run `python scripts/ingest.py` against `data/formatted/pages_latest.json`.

## Hybrid retrieval

Hybrid retrieval is optional and feature-flagged. When `HYBRID_RETRIEVAL_ENABLED=true`, the API retrieves candidates from both Chroma vector search and the local BM25 lexical index, fuses them with Reciprocal Rank Fusion, then continues through the existing diversification and reranking path. When disabled, retrieval remains vector-only.

Environment:

| Variable | Recommended staging / pilot | Recommended production | Notes |
|----------|-----------------------------|------------------------|-------|
| `HYBRID_RETRIEVAL_ENABLED` | `false` (pilot default in `.env.devops.example`, `.env.pilot.example`) | `false` until live eval shows lift | Run `python scripts/eval_retrieval.py` before enabling; set `true` only with evidence |
| `HYBRID_FAIL_OPEN` | `true` | `true` initially | Missing/corrupt BM25 index falls back to vector-only |
| `BM25_INDEX_PATH` | `./data/indexes/bm25_index.pkl` | persistent volume path | Rebuilt by `python scripts/ingest.py` |
| `BM25_TOP_K` | `20` | `20` | Lexical candidates before fusion |
| `VECTOR_TOP_K` | `20` | `20` | Vector candidates before fusion |
| `HYBRID_RRF_K` | `60` | `60` | Larger values reduce rank-position sensitivity |
| `HYBRID_FINAL_TOP_K` | `8` | `8` | Fused candidates before MMR/rerank |
| `HYBRID_MIN_BM25_SCORE` | `0.0` | `0.0` | Raise only after eval evidence |

Build/rebuild:

```bash
python scripts/ingest.py --reset-collection
```

Use `python scripts/ingest.py --skip-bm25` only when intentionally running vector-only. Retrieval logs include hybrid enabled state, vector/BM25/fused candidate counts, fail-open fallback state, and latency timings. Full visitor messages are not added to hybrid diagnostic logs.

Known limitations: BM25 improves exact-term recall but can add lexical noise, especially on broad queries. Monitor golden-question evals and production retrieval diagnostics before considering fail-closed behavior.

## Semantic reranking

| Variable | Recommended staging | Recommended production | Notes |
|----------|---------------------|------------------------|-------|
| `RERANKER_BACKEND` | `heuristic` | `heuristic` or `cohere` after eval | Default `heuristic` — no extra API key |
| `COHERE_API_KEY` | unset or set for A/B | set when using `cohere` | Cohere Rerank API via httpx |
| `COHERE_RERANK_MODEL` | `rerank-english-v3.0` | same | |
| `CROSS_ENCODER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | same | Only when `RERANKER_BACKEND=cross_encoder`; requires `sentence-transformers` (not in base `requirements.txt`) |

Heuristic reranker always remains the fallback if Cohere or cross-encoder fails.

## Query rewriting (optional)

| Variable | Default | Notes |
|----------|---------|-------|
| `QUERY_REWRITE_ENABLED` | `false` | Adds one LLM call per message before retrieval |

Enable in staging first and measure latency impact before production.

## OpenTelemetry (optional)

| Variable | Default | Notes |
|----------|---------|-------|
| `ENABLE_OTEL` | `false` | LangGraph node spans + optional FastAPI instrumentation |
| `OTLP_ENDPOINT` | unset | When set, exports traces to OTLP HTTP collector |

Install OpenTelemetry packages when enabling (`opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-exporter-otlp-proto-http`).

## Website embed

Local chat UI: [`widget/demo.html`](../widget/demo.html) — serve with `python -m http.server 8765` (localhost uses `http://127.0.0.1:8001` automatically). For production embed, set `MOBCODER_CHAT_API_URL` before loading `mobcoder-chat.js` (see [`widget/README.md`](../widget/README.md)).

See [`widget/README.md`](../widget/README.md).

## CORS

Set `CORS_ALLOWED_ORIGINS` (comma-separated). Default includes mobcoder.ai and local dev ports.

## Streaming

When `ENABLE_CHAT_STREAMING=true` and the client sends `"stream": true` on `POST /api/v1/chat/stream`, the API emits SSE events: `token`, `meta`, `done`, and optional `replace`. Without `"stream": true`, the endpoint returns a single JSON payload in one `data:` event (legacy path).

Set `MOBCODER_CHAT_STREAM=true` in the widget embed when using token streaming.

## Session store (horizontal scale)

| Variable | Recommended staging | Recommended production | Notes |
|----------|---------------------|------------------------|-------|
| `SESSION_STORE_BACKEND` | `sqlite` (single instance), `postgres`, or `redis` | `postgres` | Default `sqlite` for local/dev |
| `DATABASE_URL` | required when backend is `postgres` | required | e.g. `postgresql://user:pass@host:5432/dbname` |
| `AUTO_MIGRATE_DB` | `true` | `true` | Runs `db/migrations/*.sql` on startup / entrypoint |
| `PERSIST_ANALYTICS_EVENTS` | `true` when `DATABASE_URL` set | `true` | Writes `analytics_events` rows |
| `POSTGRES_POOL_MIN_SIZE` / `POSTGRES_POOL_MAX_SIZE` | `1` / `10` | tune per load | psycopg connection pool |
| `REDIS_URL` | unset or `redis://...` | required for `RATE_LIMIT_BACKEND=redis` | Still used for rate limiting with Postgres sessions |
| `SESSION_DB_PATH` | `./data/sessions/chat_sessions.db` | volume path if using sqlite | SQLite WAL; not shared across instances |

When `SESSION_STORE_BACKEND=postgres`, sessions, feedback, analytics, CRM dispatch audit, and conversation turns are stored in Postgres. Apply schema with `python scripts/migrate_db.py`. Schedule TTL cleanup: `python scripts/prune_sessions.py` (30-day default). Least-privilege DB roles: see `db/roles.example.sql`.

When `SESSION_STORE_BACKEND=redis`, sessions are stored as JSON blobs with a 30-day TTL. Missing `REDIS_URL` fails at first store access with an actionable error.

## Rate limiting and proxy headers

| Variable | Recommended staging | Recommended production | Notes |
|----------|---------------------|------------------------|-------|
| `RATE_LIMIT_BACKEND` | `memory` (local) or `redis` (prod-like) | `redis` | Default `memory` |
| `REDIS_URL` | required for redis backend | required | Sorted-set sliding window per client IP |
| `TRUST_PROXY_HEADERS` | `false` | `true` behind ALB/nginx that overwrites client IP | Prevents X-Forwarded-For spoofing on untrusted paths |

`TRUST_PROXY_HEADERS=false` by default. Enable only behind a trusted ingress that overwrites `X-Forwarded-For` / `X-Real-IP`.

## CRM (HubSpot) — async dispatch

Set `HUBSPOT_WEBHOOK_URL`. On `ready_for_booking=True`, the API **queues** CRM dispatch in a background thread so chat responses are never blocked by HubSpot latency or failures.

Retries use exponential backoff inside the worker. Failures are logged and emit `crm_dispatch_failed`; they never surface to the visitor.

CRM dispatch is **idempotent per session**: once a lead profile fingerprint is successfully dispatched, repeat messages with the same profile do not re-fire HubSpot or duplicate `lead_qualified` events. Failed dispatches clear the pending flag so the next qualifying turn can retry.

Qualified lead payload (no full transcript by default):

```json
{
  "source": "mobcoder_sales_assistant",
  "intent": "sales",
  "session_id": "...",
  "request_id": "...",
  "lead_score": "hot",
  "lead_score_numeric": 82,
  "meeting_readiness": "ready",
  "conversation_summary": "CTO exploring AI agent for healthcare. Timeline: 8 weeks.",
  "project_type": "ai_agent",
  "role": "cto",
  "industry": "healthcare",
  "decision_maker": true,
  "lead": {
    "name": "",
    "email": "",
    "company": "",
    "project_need": "",
    "project_type": "ai_agent",
    "timeline": "",
    "budget_band": "",
    "role": "cto",
    "industry": "healthcare",
    "decision_maker": true
  }
}
```

`conversation_summary` is generated deterministically from lead profile and recent context when a lead qualifies — no extra LLM call on every message.

## Lead intelligence

Captured on profile (deterministic rules + optional LLM extract):

- `project_type`: ai_agent, rag_chatbot, mobile_app, web_app, enterprise_software, healthcare_fintech, staff_augmentation, maintenance_support, cloud_devops, ecommerce, unknown
- `role`, `industry`, `decision_maker`
- `lead_score_numeric` (0–100), `lead_score` (hot ≥80 / warm 40–79 / cold), `meeting_readiness` (not_ready/maybe_ready/ready/booking_requested)

Internal score/readiness are included in CRM webhooks but hidden from public API responses unless `EXPOSE_INTERNAL_SALES_METADATA=true`.

## Analytics

Events via `app/observability/events.py` (stdout JSON or `ANALYTICS_WEBHOOK_URL`):

| Event | Source |
|-------|--------|
| `widget_opened` | Widget → `POST /api/v1/events` |
| `widget_opened_no_message` | Widget (opened, no user message yet) |
| `message_sent` | Widget + API on each chat |
| `qualify_shown` | Widget + API when qualify prompt shown |
| `qualify_submitted` | Widget on lead form submit |
| `booking_cta_clicked` | Widget book banner |
| `conversation_abandoned` | Widget on panel close / page unload with active chat |
| `human_escalation_requested` | Widget escalation form or API |
| `cta_clicked` | Widget suggested replies |
| `booking_intent_detected` | API when intent=booking |
| `answer_not_found` | API when retrieval empty on help/sales |
| `grounding_failure` | API when grounding fails |
| `injection_blocked` | API when prompt injection blocked |
| `help_answered` | API on help intent replies |
| `sales_answered` | API on sales intent replies |
| `lead_qualified` | API when lead complete |
| `crm_dispatch_queued` | API background CRM worker |
| `crm_dispatch_success` | API after HubSpot POST succeeds |
| `crm_dispatch_failed` | API after HubSpot retries exhausted |

Payloads include safe fields: `session_id`, `request_id`, sanitized `page_url`, `page_category`, `intent`, `project_type`, `lead_score_label`, `meeting_readiness`, UTM params. Email/name are not emitted in analytics events.

Chat requests accept optional `page_url` and `referrer` for category-aware retrieval and enriched event payloads.

## API surface and observability

In production (`APP_ENV=production`), docs and source endpoints are disabled unless explicitly enabled:

- `ENABLE_DOCS=true` enables `/docs`, `/redoc`, and `/openapi.json`.
- `ENABLE_SOURCE_ENDPOINTS=true` enables `/api/v1/sources` and `/api/v1/categories`.
- `ENABLE_DEBUG_ENDPOINTS=true` is reserved for any future debug-only routes.

Every chat request gets a `request_id` in the response and structured logs. Logs include session/request context, intent, stage, lead score, retrieval counts/scores, selected source URLs, grounding/citation status, model, streaming mode, step timings, total latency, and safe error details. Full visitor messages and lead PII are not logged. Source and page URLs in logs are sanitized to remove query strings/fragments.

Public chat responses hide internal `stage` and `lead_score` by default. To expose them for internal QA, set `EXPOSE_INTERNAL_SALES_METADATA=true` server-side and send `expose_internal_sales_metadata=true` from an internal/debug client only.

## Calendly

Set `CALENDLY_URL` in `.env`. Agent and widget surface discovery-call links.

## Apollo (optional)

Set `APOLLO_API_KEY` to enrich replies when `email` or `company` is in the lead profile.

## CRM payload and consent

Qualified lead webhooks include lead intelligence fields plus request/session/page metadata: `request_id`, `page_url`, `first_page_url`, `last_page_url`, `referrer`, UTM params, `lead_score`, `lead_score_numeric`, `meeting_readiness`, `response_stage`, `lead_consent`, `created_at`, `conversation_summary`, `project_type`, `role`, `industry`, `decision_maker`. Full transcripts are not sent by default.

The widget sends `lead_consent=true` only after the visitor submits the lead form after seeing the consent copy. Passive chat messages default to `false`.

## Google Sheets lead log (optional)

While HubSpot is pending, upsert consenting leads (email + `lead_consent`) into a Google Sheet via an Apps Script web app.

| Variable | Description |
|----------|-------------|
| `GOOGLE_SHEETS_WEBHOOK_URL` | Apps Script web-app URL (`action: upsert_lead`) |

Setup: **[docs/GOOGLE_SHEETS_LEAD_LOG.md](GOOGLE_SHEETS_LEAD_LOG.md)**. Rows refresh every turn (upsert by `session_id`). Not configured → silently disabled.

## Google Chat human-review alerts (optional)

One-time alert per session when `needs_human_review` is true and the visitor gave consent. Uses the same hot-lead threshold as `app/agent/routing.py`.

| Variable | Description |
|----------|-------------|
| `GOOGLE_CHAT_WEBHOOK_URL` | Google Chat space incoming webhook URL |

Setup: **[docs/GOOGLE_CHAT_LEAD_ALERTS.md](GOOGLE_CHAT_LEAD_ALERTS.md)**. Set in `.env.production` on EC2 (not in Terraform yet).

## Human escalation

`POST /api/v1/escalate` accepts name, email, message, optional `session_id` and `page_url`. Dispatches to HubSpot with `source: "human_escalation"`. Widget config: `MOBCODER_ESCALATE_URL` (auto-derived from chat URL if omitted).

## Eval gate (pre-release)

```bash
python3 scripts/run_eval.py --min-pass-rate 0.90
```

Use `--require-perfect` only when any single failed case should fail the build despite meeting the configured pass-rate threshold.
The eval runner preflights `OPENAI_API_KEY`, eval dataset existence, and non-empty vector store so setup failures are not reported as model-quality failures.

## Staging checklist

- Set `APP_ENV=production`, `OPENAI_API_KEY`, `CORS_ALLOWED_ORIGINS`, `CHROMA_PERSIST_DIR`.
- **Pilot stack** (`.env.staging.example`, `.env.devops.example`): `OPERATING_MODE=help`, `ENABLE_LEAD_QUALIFICATION=false`, `HYBRID_RETRIEVAL_ENABLED=false`.
- Single instance: `SESSION_STORE_BACKEND=sqlite`, `RATE_LIMIT_BACKEND=memory`.
- Multi-instance staging: `SESSION_STORE_BACKEND=redis`, `RATE_LIMIT_BACKEND=redis`, `REDIS_URL=...`.
- Keep `HYBRID_RETRIEVAL_ENABLED=false`, `EXPOSE_INTERNAL_SALES_METADATA=false`.
- Keep `ENABLE_DOCS=false`, `ENABLE_SOURCE_ENDPOINTS=false`, `ENABLE_DEBUG_ENDPOINTS=false`.
- Set `HUBSPOT_WEBHOOK_URL` when testing CRM delivery.
- Run `python3 scripts/crawl_mobcoder.py`, `python3 scripts/build_knowledge_md.py`, and `python3 scripts/ingest.py`.
- Verify `GET /api/v1/health` reports a nonzero vector count.
- If hybrid retrieval is enabled, confirm `data/indexes/bm25_index.pkl` exists after ingestion and keep `HYBRID_FAIL_OPEN=true`.
- Run `pytest` and `python3 scripts/run_eval.py --min-pass-rate 0.90`.
- Embed the widget with HTTPS API URL and keep `MOBCODER_SHOW_DEBUG_SALES_STATE=false`.
- Roll back by reverting to the previous API revision and previous widget asset/CDN version.

Offline smoke: `pytest tests/test_eval_gate.py`

## Docker staging (Redis + multi-instance)

Two API replicas sharing Redis (ports 8001 and 8002):

```bash
cp .env.staging.example .env.staging
# Edit .env.staging with OPENAI_API_KEY and secrets
docker compose -f docker-compose.staging.yml --env-file .env.staging up --build
```

Automated smoke checks (in-process, no Docker required):

```bash
python3 scripts/staging_smoke.py
STAGING_API_URL=http://127.0.0.1:8001 python3 scripts/staging_smoke.py --live
```

## Production deploy checklist

1. Provision managed Redis; set `REDIS_URL`, `SESSION_STORE_BACKEND=redis`, `RATE_LIMIT_BACKEND=redis`.
2. Set `HUBSPOT_WEBHOOK_URL`, optional `ANALYTICS_WEBHOOK_URL`, optional `APOLLO_API_KEY`.
3. Deploy ≥2 Cloud Run / K8s replicas with shared `./data` volume or rebuilt Chroma index.
4. Run `python3 scripts/staging_smoke.py --live` against staging URL.
5. Run `python3 scripts/run_eval.py --min-pass-rate 0.90` before promotion.
6. Deploy widget with `MOBCODER_SHOW_DEBUG_SALES_STATE=false`.
7. Monitor logs for `crm_dispatch_success`, `crm_dispatch_failed`, Redis errors.

Example Cloud Run deploy:

```bash
gcloud run deploy mobcoder-assistant \
  --source . \
  --region us-central1 \
  --min-instances 2 \
  --set-env-vars "APP_ENV=production,SESSION_STORE_BACKEND=redis,RATE_LIMIT_BACKEND=redis,REDIS_URL=redis://...,HYBRID_RETRIEVAL_ENABLED=false,EXPOSE_INTERNAL_SALES_METADATA=false" \
  --set-secrets "OPENAI_API_KEY=openai-key:latest,HUBSPOT_WEBHOOK_URL=hubspot-url:latest"
```

## Scheduled re-crawl

GitHub Action: [`.github/workflows/weekly-crawl.yml`](../.github/workflows/weekly-crawl.yml) (Mondays 06:00 UTC) or cron:

```bash
python scripts/crawl_mobcoder.py
python scripts/build_knowledge_md.py
python scripts/ingest.py
```

Recovery without re-crawl: `python scripts/reformat_from_raw.py`

## Grounding

- Default: heuristic overlap in `app/agent/grounding.py`
- `ENABLE_LLM_GROUNDING=true` for optional LLM claim check
- `GROUNDING_PROVIDER=sovereign` uses Sovereign stub (delegates to heuristic until integrated)

## Environment reference

See [`.env.example`](../.env.example).
