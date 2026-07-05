# MobCoder Sales & Help Assistant

RAG-powered **Sales + Help** chatbot for [mobcoder.ai](https://mobcoder.ai/). It crawls official site content, embeds it in Chroma, and answers through a LangGraph agent exposed via **FastAPI** and an embeddable **website widget**.

**Pilot (Phase 1):** Help-only via `OPERATING_MODE=help` — retrieve-first answers, no inline qualify on the hot path. See `.env.pilot.example`.

**Phase 2 (default):** Full sales layer via `OPERATING_MODE=sales` or `full` — progressive qualification, lead scoring, CRM dispatch, Sheets/Chat alerts.

## Features

| Feature | How it works in this repo |
|---------|---------------------------|
| **Grounded answers** | RAG over crawled mobcoder.ai pages (Chroma vectors; optional BM25 + RRF hybrid, heuristic/Cohere/cross-encoder rerank, MMR diversity). Empty index falls back to bundled seed chunks (`app/rag/seed_fallback.py`). |
| **Page-aware retrieval** | `page_url` / category bias via `retrieval_plan.py` and optional `PAGE_CONTEXT_BOOST_ENABLED` (default `true`). |
| **Fast first token (streaming)** | Widget default: `POST /chat/stream` runs safety + retrieve before any LLM call; tokens stream via `stream_runner.py`. |
| **Answer validation** | Heuristic LLM grounding (`ENABLE_LLM_GROUNDING`), citation checks, scope redirect when retrieval is empty. |
| **Operating modes** | `help` — no inline qualify, help intent on hot path. `sales` / `full` — soft qualification questions, pricing CTAs, full lead layer. |
| **Lead profiling (no form)** | Regex + incremental extract on every turn; LLM profile extract in `update_lead_profile` (sync on `POST /chat`, async after stream). |
| **Lead scoring & routing** | Rule-based `compute_lead_scoring()` on the hot path; `decide_routing()` sets `cta_type` (`instant_booking`, `qualify`, `human_handoff`, `educate`). |
| **Human escalation** | Frustration / “talk to a person” detection; widget form → `POST /escalate` → HubSpot (`source=human_escalation`). |
| **Booking CTAs** | Calendly link when readiness + score thresholds met (`CALENDLY_URL`). |
| **Suggested replies** | Heuristic chips + optional LLM chips (`LLM_SUGGESTED_REPLIES_ENABLED`, default `true`; disabled in pilot). |
| **Sessions & attribution** | SQLite (local), Postgres (production), or Redis (multi-instance). Stores IP, UTM, timezone, scroll depth, page URLs in session metadata. |
| **Analytics** | Client events (`POST /events`) and server events → stdout, optional Postgres (`PERSIST_ANALYTICS_EVENTS`), optional webhook. |
| **Feedback** | Thumbs up/down + optional comment → Postgres/SQLite; stats via `/admin` or internal API (`INTERNAL_API_KEY`). |
| **CRM & lead visibility** | Async HubSpot dispatch with conversation summary; Google Sheets upsert (consent + email); one-time Google Chat alert for hot human-review leads. |
| **Apollo enrichment** | Optional email/company match on dispatch; optional async IP-to-company on new sessions. |
| **Embeddable widget** | `mobcoder-chat.js` — SSE streaming, proactive auto-open (`MOBCODER_AUTO_OPEN_DELAY_SECONDS`), exit-intent badge, EU AI Act disclosure copy. |
| **Ops portal** | Read-only `/admin` (feedback stats, feedback list, leads) — data calls require `X-Internal-Key`. |
| **Production hardening** | Per-IP rate limits (memory or Redis), CORS allowlist, proxy-aware client IP, optional OpenTelemetry. |

Product-oriented overview: **[PRODUCT.md](PRODUCT.md)**.

## System architecture

```mermaid
flowchart TB
  subgraph clients [Clients]
    Widget["Website widget"]
  end

  subgraph api [FastAPI]
    ChatRoutes["POST /chat and /chat/stream"]
    EventRoutes["POST /events"]
    FeedbackRoute["POST /feedback"]
    EscalateRoute["POST /escalate"]
    RateLimit["RateLimitMiddleware"]
    ChatService["chat_service"]
  end

  subgraph agent [Agent hot path — streaming default]
    Safety["safety_check"]
    Retrieve["retrieve_sources via retrieval_plan"]
    Generate["generate_answer / stream"]
    Ground["validate_grounding"]
    Citations["validate_citations"]
    Qualify["append_qualification"]
    Final["build_final_response"]
    Safety --> Retrieve --> Generate --> Ground --> Citations --> Qualify --> Final
  end

  subgraph post [Post-response async]
    Enrich["post_response.enrich_state"]
    Intent["classify_intent"]
    Profile["update_lead_profile"]
    Route["decide_routing"]
    Persist["persist_qualification"]
    Enrich --> Intent --> Profile --> Route --> Persist
  end

  subgraph storage [Storage]
    Chroma["Chroma vector store"]
    SessionSQLite["SQLite sessions default"]
    SessionPostgres["Postgres sessions production"]
    SessionRedis["Redis sessions optional"]
    RedisRL["Redis rate limit production"]
  end

  subgraph async [Background async]
    Summary["conversation_summary"]
    CRM["dispatch_qualified_lead_async"]
    HubSpot["HubSpot webhook"]
    Sheets["Google Sheets lead log"]
    ChatAlert["Google Chat alert"]
    Analytics["analytics events"]
  end

  Widget --> ChatRoutes
  Widget --> EventRoutes
  Widget --> FeedbackRoute
  Widget --> EscalateRoute
  ChatRoutes --> RateLimit
  RateLimit --> ChatService
  RateLimit -.-> RedisRL
  ChatService --> agent
  Retrieve --> Chroma
  ChatService --> SessionSQLite
  ChatService --> SessionPostgres
  ChatService --> SessionRedis
  ChatService --> Enrich
  Persist --> Sheets
  Persist --> ChatAlert
  ChatService --> Summary
  Summary --> CRM
  CRM --> HubSpot
  ChatService --> Analytics
```

> Diagrams use [Mermaid](https://mermaid.js.org/) fences (` ```mermaid `). They render on GitHub and in editors with Mermaid support.

> **Retrieve-first hot path.** Both paths run `safety_check → retrieve_sources → generate → validate → qualify → build_final_response` with **rule-based** lead scoring and `decide_routing` so the visitor always gets a CTA on the hot path. **Streaming** (`POST /chat/stream`, widget default): LLM intent/profile enrichment runs **after** the response in a background thread (`dispatch_post_response_enrichment` → `enrich_state`). **Sync** (`POST /chat`): `run_agent()` calls `enrich_state` **before** returning, then persists Sheets/Chat alerts async (`dispatch_qualification_persistence`). Pilot help mode (`OPERATING_MODE=help`) skips inline qualify and forces help defaults on the hot path; LLM profile extraction runs only when `OPERATING_MODE` is `sales` or `full` and `ENABLE_LEAD_QUALIFICATION=true`.

## What it does

| Mode | Purpose | Example visitor questions |
|------|---------|---------------------------|
| **Help** | Educate about services, process, case studies | “What AI services do you offer?” |
| **Sales** | Consult on projects, pricing, vendor comparison | “We need an AI support chatbot — can you help?” |
| **Booking** | Schedule a discovery call | “I'd like to book a discovery call.” |

The agent **always retrieves mobcoder.ai sources before answering** (with seed fallback if the vector store is empty). Default `OPERATING_MODE=full` enables the full sales layer. In **Phase 2** (`sales` or `full`), it may append one soft qualification question after rapport. In **pilot help mode** (`help`), inline qualification is disabled and help intent is pinned on the hot path.

## End-to-end workflow

```mermaid
flowchart LR
  Env[".env keys OPENAI Apify"] --> Knowledge["Knowledge seed or crawl"]
  Knowledge --> Ingest["Ingest Chroma plus BM25 index"]
  Ingest --> Run["Run API or widget"]
```

1. Configure `.env` (minimum: `OPENAI_API_KEY` for chat).
2. Load knowledge (seed **or** full Apify crawl + ingest).
3. Verify `GET /api/v1/health` shows `vector_store_count > 0`.
4. Run the API; optional: open `widget/demo.html` for the chat UI.

## Quick start

```bash
cd mobcoder.ai-agent-dev   # or your clone directory
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum set OPENAI_API_KEY
```

### 1. Knowledge (pick one path)

#### A. Quick start (no Apify) — ~10 bundled pages

```bash
python scripts/seed_knowledge.py          # copy seed → pages_latest + markdown
python scripts/seed_knowledge.py --ingest # embed into Chroma (needs OPENAI_API_KEY)
```

Works immediately with **seed fallback** retrieval even before `--ingest`.

#### B. Full mobcoder.ai crawl (recommended for production)

Requires `APIFY_API_TOKEN` and `OPENAI_API_KEY`.

```bash
# Discover sitemap URLs (optional audit)
python scripts/discover_urls.py

# Crawl all sitemap + seed URLs (sitemap count varies; crawler also follows links)
python scripts/crawl_mobcoder.py --use-sitemap --max-pages 500

# Human-readable bundle (optional)
python scripts/build_knowledge_md.py

# Chunk, embed, load Chroma, merge supplemental JSON, build BM25 index
# (use --reset-collection for a clean re-ingest; --skip-bm25 for vector-only)
python scripts/ingest.py --reset-collection

# Confirm coverage
python scripts/discover_urls.py   # target: "Missing from last crawl: 0"
```

**Ingest pipeline details:**

- **404 / error pages** are dropped at load time (`app/crawler/page_loader.py`) so crawled error HTML never becomes chunks.
- **Supplemental knowledge** in `data/formatted/supplemental/*.json` is merged during ingest (append into an existing URL or add as a new page). Shipped example: `mobcoder_ai_capabilities.json`.
- **Crawl drift warnings** — ingest logs retrieval slug mismatches via `check_retrieval_slug_dependencies()`.
- **Manifest** — `data/formatted/ingest_manifest.json` tracks `last_crawl_at`, `last_ingest_at`, chunk/page counts, BM25 path (preserves crawl fields across re-ingest).

Last verified full re-ingest (July 2026): **~101 pages → ~257 chunks** after supplemental merge. A handful of sitemap URLs may intermittently miss a given Apify run — re-run crawl if `discover_urls.py` reports missing URLs.

Outputs (gitignored locally): `data/raw/`, `data/formatted/pages_latest.json`, `data/chunks/chunks_*.json`, `data/embeddings/chroma/`, `data/indexes/bm25_index.pkl`, `data/formatted/ingest_manifest.json`.

### Hybrid retrieval

Retrieval is **vector-only by default** (`HYBRID_RETRIEVAL_ENABLED=false`). When hybrid is enabled, Chroma semantic search is combined with a local BM25 index and fused with Reciprocal Rank Fusion (RRF), then reranked (`RERANKER_BACKEND=heuristic|cohere|cross_encoder`) and diversified with MMR. Optional LLM query rewrite (`QUERY_REWRITE_ENABLED=false` by default) runs before retrieval when enabled.

Build or rebuild the BM25 index during ingestion:

```bash
python scripts/ingest.py --reset-collection
# or skip lexical index build for a vector-only ingest
python scripts/ingest.py --skip-bm25
```

Enable hybrid retrieval:

```bash
HYBRID_RETRIEVAL_ENABLED=true
HYBRID_FAIL_OPEN=true
```

If the BM25 index is missing, corrupt, or empty and `HYBRID_FAIL_OPEN=true`, the retriever logs a warning and falls back to vector-only retrieval. Keep vector-only available as the immediate rollback path by setting `HYBRID_RETRIEVAL_ENABLED=false`.

### 2. Run the API

**Foreground (recommended for development)** — keep the terminal open:

```bash
./scripts/start_api.sh
# or: python main.py
```

- Swagger: http://127.0.0.1:8001/docs  
- Health: http://127.0.0.1:8001/api/v1/health  

**Background (one terminal for curl tests):**

```bash
API_RELOAD=false nohup python main.py >> /tmp/mobcoder-api.log 2>&1 &
sleep 4 && curl -s http://127.0.0.1:8001/api/v1/health | python3 -m json.tool
```

If `curl` fails with “Couldn't connect”, the API process is not running — start it first.

### 2b. PostgreSQL sessions (recommended for production parity)

Use Postgres for durable sessions, feedback, analytics events, CRM dispatch audit, and conversation turns.

**Windows (local Postgres 18):**

```powershell
$env:Path += ";C:\Program Files\PostgreSQL\18\bin"
.\scripts\setup_postgres.ps1 -PostgresPassword 'your-postgres-superuser-password'
```

**Apply schema:**

```bash
python scripts/migrate_db.py
```

**`.env`:**

```bash
SESSION_STORE_BACKEND=postgres
DATABASE_URL=postgresql://mobcoder:mobcoder%40123@localhost:5432/mobcoder
PERSIST_ANALYTICS_EVENTS=true
```

(URL-encode `@` in passwords as `%40`.)

**Inspect data:**

```bash
python scripts/inspect_db.py
```

**Visitor attribution** (`demo.html`):

| Stored in `chat_sessions.metadata` | Source |
|-----------------------------------|--------|
| `first_client_ip`, `last_client_ip` | Server extracts from request (set `TRUST_PROXY_HEADERS=true` behind ALB/nginx) |
| `user_agent` | Request header |
| `visitor_timezone`, `visitor_language`, `visitor_scroll_depth_pct` | Widget `visitor_meta` on first chat (or `/events` from demo) |
| `utm_params`, `visitor_referrer` | Page URL + widget fingerprint |
| `page_url`, `first_page_url`, `last_page_url` | Chat / events |

`analytics_events.client_ip` is populated when `PERSIST_ANALYTICS_EVENTS=true`.

**pgAdmin quick check:**

```sql
SELECT session_id, updated_at,
       metadata->>'first_client_ip' AS ip,
       metadata->>'visitor_timezone' AS tz,
       metadata->'utm_params' AS utm
FROM chat_sessions ORDER BY updated_at DESC LIMIT 10;
```

Schedule TTL cleanup: `python scripts/prune_sessions.py` (30-day default). See **[docs/PRODUCTION.md](docs/PRODUCTION.md)** for RDS/Terraform wiring.

### 3. Website widget

See **[widget/README.md](widget/README.md)** — local `widget/demo.html`, `contact-us.html`, `pricing.html`. Production embed: **`mobcoder-chat.js`** via **`embed-snippet.html`**. Rollout: **[docs/WEBSITE_BOT.md](docs/WEBSITE_BOT.md)**.

| Widget config | Purpose |
|---------------|---------|
| `MOBCODER_CHAT_API_URL` | Chat endpoint (auto-detected on mobcoder.ai / localhost when omitted) |
| `MOBCODER_CHAT_STREAM` | SSE token streaming (default `true`) |
| `MOBCODER_AUTO_OPEN_DELAY_SECONDS` | Proactive panel open on high-intent pages (default `45`, `0` = off) |
| `MOBCODER_CALENDLY_URL` / `MOBCODER_CONTACT_URL` | Booking vs contact CTAs |
| `MOBCODER_PRIVACY_COPY` | Lead-form consent text |
| `MOBCODER_SHOW_DEBUG_SALES_STATE` | Debug metadata in UI (keep `false` in production) |

The widget discloses **“AI assistant”** in the header and openers (EU AI Act Art. 50(1)). Do not remove when customizing embeds. Proactive auto-open and exit-intent are suppressed on `/contact-us`, when a lead is known, or after qualification.

Production deploy notes: **[docs/PRODUCTION.md](docs/PRODUCTION.md)** (Docker, EC2, ECS, CORS, HubSpot, Google Sheets/Chat). Enhancement roadmap: **[docs/enhancement_review.md](docs/enhancement_review.md)** (P0/P1 complete).

### 4. Evaluation

```bash
python3 scripts/run_eval.py --min-pass-rate 0.90
pytest tests/ -q
```

## API usage

Base path: `/api/v1`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Vector store status, chunk count, last crawl/ingest times |
| `POST` | `/chat` | Main chat (JSON body below) |
| `POST` | `/chat/stream` | SSE token stream (when `ENABLE_CHAT_STREAMING=true`) |
| `POST` | `/events` | Client analytics (widget funnel, UTM-attributed) |
| `POST` | `/feedback` | Thumbs up/down (`rating`: `1` or `-1`) for a bot message |
| `POST` | `/escalate` | Human escalation form → HubSpot (`source=human_escalation`) |
| `GET` | `/widget-context` | Opener text + starter chips for widget (`page_url` required; optional `page_title`) |
| `GET` | `/sources` | Indexed source URLs; disabled in production unless `ENABLE_SOURCE_ENDPOINTS=true` |
| `GET` | `/categories` | Page categories for retrieval bias; disabled in production unless `ENABLE_SOURCE_ENDPOINTS=true` |
| `GET` | `/feedback` | Internal: list feedback records; requires `X-Internal-Key` when `INTERNAL_API_KEY` is set |
| `GET` | `/feedback/stats` | Internal: aggregate thumbs totals; same key requirement |
| `GET` | `/leads` | Internal: sessions with name or email; same key requirement |

Also: **`GET /admin`** — read-only ops UI (feedback stats, feedback list, leads). Uses the same internal key on API calls from the page.

### Chat request

```bash
curl -s -X POST http://127.0.0.1:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tell me more about your agentic AI capabilities",
    "page_url": "https://mobcoder.ai/services/agentic-ai"
  }' | python3 -m json.tool
```

| Field | Required | Description |
|-------|----------|-------------|
| `message` | Yes | Visitor message |
| `request_id` | No | Optional caller-provided trace id; generated when omitted |
| `page_url` | No | Current page — improves retrieval (`ai_agents`, `services`, etc.) |
| `referrer` | No | Referrer/source URL for CRM context |
| `lead_consent` | No | True only when visitor submits lead details after seeing consent copy |
| `expose_internal_sales_metadata` | No | Debug-only; honored only when server `EXPOSE_INTERNAL_SALES_METADATA=true` |
| `session_id` | No | Omitted → server creates one; reuse for multi-turn + lead profile |
| `conversation_history` | No | Prior `{role, content}` turns if not using server session store |
| `lead_profile` | No | `name`, `email`, `company`, `role`, `industry`, `project_need`, `project_type`, `timeline`, `budget_band`, `decision_maker` |
| `visitor_meta` | No | Passive fingerprint: `timezone`, `language`, `scroll_depth_pct`, UTM fields, `referrer` (widget sends on first chat) |
| `stream` | No | Request token streaming on `/chat/stream` when `ENABLE_CHAT_STREAMING=true` (widget sends `true`) |

### Chat response (high level)

| Field | Meaning |
|-------|---------|
| `response` | Markdown answer (may include **Sources** links) |
| `request_id` | Trace id included in structured API logs |
| `intent` | `help` \| `sales` \| `booking` \| `general` |
| `stage` | `null` by default; internal/debug only when `EXPOSE_INTERNAL_SALES_METADATA=true` |
| `lead_score` | `null` by default; internal/debug only when `EXPOSE_INTERNAL_SALES_METADATA=true` |
| `lead_bucket` | `hot` \| `warm` \| `cold`; same gate as `lead_score` (debug metadata) |
| `cta_type` | Routing CTA: `instant_booking` \| `qualify` \| `human_handoff` \| `educate` |
| `citations` | Retrieved mobcoder.ai chunks |
| `suggested_replies` | Follow-up chip labels |
| `show_human_escalation` | When true, widget may show “Talk to our team” |
| `needs_contact_info` | A soft qualify question was appended |
| `ready_for_booking` | Lead complete for sales/booking/help MQL |

### Feedback

**Submit** (widget or API):

```bash
curl -s -X POST http://127.0.0.1:8001/api/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "your-session-id",
    "message_id": "request-id-of-that-turn",
    "rating": 1,
    "comment": "Helpful answer"
  }' | python3 -m json.tool
```

| Field | Required | Description |
|-------|----------|-------------|
| `session_id` | No | Reuse chat session; created if omitted |
| `message_id` | Yes | Stable ID for the rated bot turn (e.g. that turn's `request_id`) |
| `rating` | Yes | `1` = thumbs up, `-1` = thumbs down |
| `comment` | No | Optional free-text note (max 1000 chars) |

**Read** (internal QA / admin — Postgres backend recommended):

Set `INTERNAL_API_KEY` in `.env`, then call with header `X-Internal-Key: <your-key>`. When the key is unset, `GET /feedback*`, `GET /leads`, and `/admin` data loads return `404` / fail.

```bash
curl -s http://127.0.0.1:8001/api/v1/feedback/stats \
  -H "X-Internal-Key: your-internal-key" | python3 -m json.tool
```

## Agent pipeline

**Streaming (widget default)** — `POST /chat/stream` with `stream: true` and `ENABLE_CHAT_STREAMING=true`:

```mermaid
flowchart LR
  A[safety_check]
  B[retrieve_sources]
  C[stream generate_answer]
  D[validate_grounding]
  E[validate_citations]
  F[append_qualification]
  G[build_final_response + rule scoring + routing]
  H[background enrich_state + persist]
  A --> B --> C --> D --> E --> F --> G --> H
```

**Sync** — `POST /chat` via LangGraph (`app/agent/graph.py`): same hot-path nodes plus `prepare_defaults` in help mode; `enrich_state` runs **synchronously** inside `run_agent()` before the JSON response; Sheets/Chat persistence runs in a background thread.

After the visitor sees the response:

- **Stream path:** `dispatch_post_response_enrichment` → `classify_intent`, `update_lead_profile`, recomputed `decide_routing`, `persist_qualification` (session metadata, Google Sheets, Google Chat).
- **Sync path:** enrichment already applied; `dispatch_qualification_persistence` writes metadata and fires outbound alerts only.

Retrieval uses rule-based `retrieval_plan.py` (topic + `source_tier` filters) — **no LLM before first token** on the streaming hot path.

- **Help pilot** (`OPERATING_MODE=help`): no inline qualify; help intent/stage pinned on hot path; regex-only profile extract in enrichment (no profile LLM).
- **Sales / full**: project → timeline → budget → contact fields via `append_qualification`.
- **Booking**: name, email, company; Calendly CTA when configured.
- **Off-topic** queries with zero retrieval hits get a scope redirect after classify.
- **Routing** (`app/agent/routing.py`): `cta_type` and `needs_human_review` from lead score + intent; hot enterprise/value leads can trigger a one-time Google Chat alert (requires `lead_consent`).
- **HubSpot**: async CRM webhook when a lead qualifies (`HUBSPOT_WEBHOOK_URL`); includes `conversation_summary.py` excerpt; non-blocking.
- **Google Sheets**: upsert consenting leads with email (`GOOGLE_SHEETS_WEBHOOK_URL`); see **[docs/GOOGLE_SHEETS_LEAD_LOG.md](docs/GOOGLE_SHEETS_LEAD_LOG.md)**.
- **Google Chat**: one-time human-review alert per session (`GOOGLE_CHAT_WEBHOOK_URL`); see **[docs/GOOGLE_CHAT_LEAD_ALERTS.md](docs/GOOGLE_CHAT_LEAD_ALERTS.md)**.
- **CRM dispatch** is triggered from `chat_service.py` when `ready_for_booking` / qualification thresholds are met — not on every turn.

## Architecture

Production runtime (Postgres sessions + Redis rate limiting; staging may use Redis sessions):

```mermaid
flowchart TB
  Widget[Widget] -->|"POST /chat"| API[FastAPI]
  Widget -->|"POST /events"| API
  API --> RL[Rate limiter]
  RL --> CS[chat_service]
  CS --> LG[LangGraph agent]
  CS --> SS[Session store]
  SS --> SQL[(SQLite)]
  SS --> PG[(PostgreSQL)]
  SS --> RD[(Redis)]
  RL --> RD
  LG --> VS[Chroma RAG]
  CS -->|"qualified lead"| SUM[Summary]
  SUM --> CRM[Async CRM worker]
  CRM --> HS[HubSpot]
  CS --> PostEnrich[post_response enrich + persist]
  PostEnrich --> Sheets[Google Sheets]
  PostEnrich --> GChat[Google Chat]
  CS --> EV[Analytics]
```

**Chat hot path (streaming, default):** safety → retrieve → stream answer → validate → qualify → final (with inline rule-based scoring + routing). LLM intent/profile enrichment and outbound persistence run **after** the response on the stream path; sync `/chat` enriches before return. CRM, Sheets, Chat, and analytics webhooks never block the visitor.

**Knowledge & storage:**

- **Apify** `website-content-crawler` → `data/raw/` (404/error pages filtered at load)
- **Chunk + embed + supplemental merge** → Chroma collection `mobcoder_sales`
- **Chunk snapshots** → `data/chunks/chunks_<timestamp>.json` (used by `/sources`)
- **Sessions** → SQLite (local default), **PostgreSQL** (production: `SESSION_STORE_BACKEND=postgres`), or Redis (multi-instance smoke tests)
- **Analytics / turns / feedback** → Postgres tables when `DATABASE_URL` + `PERSIST_ANALYTICS_EVENTS=true`
- **FastAPI** and **widget** share retrieve-first logic (`stream_runner` for SSE; `graph.run_agent()` for sync)
- **First boot (Docker):** `AUTO_INGEST_ON_START=true` seeds knowledge when Chroma is empty (`scripts/docker_entrypoint.sh`)

**Staging (Redis + 2 replicas):** see [docs/PRODUCTION.md](docs/PRODUCTION.md) and `docker-compose.staging.yml`.

## Project layout

```text
app/agent/          LangGraph nodes, routing, retrieval_plan, post_response, lead_intelligence
app/api/            FastAPI routes, chat service, attribution, history sanitization
app/rag/            Retriever, embeddings, vector store, BM25, fusion, reranker, seed_fallback
app/crawler/        Scrape, chunk, deduplicate, page categories, source_tier, 404 filtering
app/sessions/       SQLite, Postgres, and Redis session stores
app/infra/          Postgres pool, Redis client
app/integrations/   HubSpot, Apollo, Google Sheets lead log, Google Chat alerts
app/middleware/     Rate limiting
app/observability/  Analytics events, OpenTelemetry tracing
app/static/         admin.html (read-only ops portal at /admin)
app/config/         Pydantic settings (OPERATING_MODE, hybrid retrieval)
scripts/            crawl, ingest, eval, pilot_smoke.sh, run_dev.sh, migrate_db
db/migrations/      Postgres schema (001_core, 002_analytics, 003_client_ip)
deploy/terraform/   AWS VPC, RDS, Redis, EFS, ECS, ALB
widget/             mobcoder-chat.js, demo.html, embed-snippet.html
prompts/            System prompt for the sales assistant
data/evals/         golden_questions.json, retrieval_queries.json
data/formatted/supplemental/  Optional JSON knowledge merged at ingest
docs/               PRODUCTION.md, DEVOPS_DEPLOY.md, GOOGLE_SHEETS_LEAD_LOG.md, GOOGLE_CHAT_LEAD_ALERTS.md
.env.pilot.example  Help-only pilot config (copy or merge for local/devapi)
PRODUCT.md          Product-facing feature summary
```

## Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes (chat + ingest) | LLM and embeddings |
| `APIFY_API_TOKEN` | Crawl only | Apify website crawler |
| `OPENAI_MODEL` | No | Default `gpt-5-mini` (GPT-5/o-series params handled automatically; legacy `gpt-4o-*` still supported) |
| `OPENAI_EMBEDDING_MODEL` | No | Default `text-embedding-3-small` |
| `RETRIEVAL_TOP_K` | No | Final chunks after rerank; default `8` |
| `RETRIEVAL_CANDIDATE_K` | No | Initial retrieval pool before MMR/rerank; default `16` |
| `RERANKER_TOP_K` | No | Chunks after reranker; default `4` |
| `APP_ENV` | No | `development` (default) or `production`; gates docs/source/debug endpoints |
| `AUTO_MIGRATE_DB` | No | Apply `db/migrations/` on startup when `DATABASE_URL` set; default `true` |
| `POSTGRES_POOL_MIN_SIZE` | No | Postgres pool min connections; default `1` |
| `POSTGRES_POOL_MAX_SIZE` | No | Postgres pool max connections; default `10` |
| `CALENDLY_URL` | No | Booking link in CTAs |
| `CONTACT_PAGE_URL` | No | Contact page link in escalation copy |
| `BOOKING_CTA` | No | CTA label text |
| `OPERATING_MODE` | No | `help` (pilot), `sales`, or `full` (default). Help disables inline qualify on hot path. See `.env.pilot.example`. |
| `PAGE_CONTEXT_BOOST_ENABLED` | No | Boost retrieval for current page URL/category; default `true` (pilot sets `false`) |
| `LLM_SUGGESTED_REPLIES_ENABLED` | No | LLM-generated follow-up chips; default `true` (pilot sets `false`) |
| `TRUST_CLIENT_CONVERSATION_HISTORY` | No | Default `false` — server session history always wins over client-supplied `conversation_history` (anti-spoofing) |
| `GROUNDING_REPLACE_ON_FAILURE` | No | Rewrite streamed answer when grounding fails; default `false` (log only) |
| `ENABLE_LEAD_QUALIFICATION` | No | Soft qualify questions after rapport; default `true`; set `false` for help pilot |
| `ENABLE_LLM_GROUNDING` | No | Validate answers against retrieved context; default `true` |
| `GROUNDING_PROVIDER` | No | `heuristic` (default); `sovereign` is currently a stub that delegates to heuristic |
| `HUBSPOT_WEBHOOK_URL` | No | Qualified lead webhook |
| `GOOGLE_SHEETS_WEBHOOK_URL` | No | Apps Script URL for lead-log sheet upserts; see `docs/GOOGLE_SHEETS_LEAD_LOG.md` |
| `GOOGLE_CHAT_WEBHOOK_URL` | No | Google Chat space webhook for one-time human-review alerts; see `docs/GOOGLE_CHAT_LEAD_ALERTS.md` |
| `APOLLO_API_KEY` | No | Optional lead enrichment (email/company match) |
| `APOLLO_IP_ENRICHMENT_ENABLED` | No | Async IP-to-company enrichment on new sessions; default `false` |
| `INTERNAL_API_KEY` | No | Gates `GET /feedback`, `/feedback/stats`, `/leads`, and `/admin` data loads; unset = those endpoints return `404` |
| `ENABLE_CHAT_STREAMING` | No | SSE token stream on `/chat/stream`; default `true` |
| `API_RELOAD` | No | Uvicorn reload in dev; default `false` in `.env.example` |
| `ENABLE_DOCS` | No | Swagger UI; default off in production |
| `ENABLE_SOURCE_ENDPOINTS` | No | `/sources` and `/categories`; default off in production |
| `ENABLE_DEBUG_ENDPOINTS` | No | Debug routes; default off in production |
| `CORS_ALLOWED_ORIGINS` | No | Comma-separated origins for widget |
| `ANALYTICS_WEBHOOK_URL` | No | Server analytics JSON webhook |
| `SESSION_STORE_BACKEND` | No | `sqlite` (default), `postgres`, or `redis` |
| `DATABASE_URL` | When postgres | e.g. `postgresql://user:pass@host:5432/mobcoder` |
| `PERSIST_ANALYTICS_EVENTS` | No | Write `analytics_events` (+ `client_ip`) to Postgres when `DATABASE_URL` is set; default `true` |
| `REDIS_URL` | When redis backends | Shared Redis for sessions and/or rate limiting |
| `RATE_LIMIT_ENABLED` | No | Enable per-IP rate limiting on chat/events/feedback/escalate; default `true` |
| `RATE_LIMIT_PER_MINUTE` | No | Default cap per IP per minute; default `30` |
| `RATE_LIMIT_BACKEND` | No | `memory` (default) or `redis` for distributed limits |
| `RATE_LIMIT_CHAT_PER_MINUTE` | No | Override for `/chat` and `/chat/stream`; default = `RATE_LIMIT_PER_MINUTE` |
| `RATE_LIMIT_EVENTS_PER_MINUTE` | No | Override for `/events`; default = `RATE_LIMIT_PER_MINUTE` |
| `RATE_LIMIT_FEEDBACK_PER_MINUTE` | No | Override for `/feedback`; default = `RATE_LIMIT_PER_MINUTE` |
| `RATE_LIMIT_ESCALATE_PER_MINUTE` | No | Override for `/escalate`; default = `RATE_LIMIT_PER_MINUTE` |
| `TRUST_PROXY_HEADERS` | No | Trust proxy IP headers only behind a sanitizing ingress |
| `EXPOSE_INTERNAL_SALES_METADATA` | No | Allows debug clients to request `stage`/`lead_score` |
| `HYBRID_RETRIEVAL_ENABLED` | No | Enables vector + BM25 retrieval with RRF fusion; default `false` |
| `BM25_INDEX_PATH` | No | Local BM25 pickle path; default `./data/indexes/bm25_index.pkl` |
| `BM25_TOP_K` | No | BM25 candidate count before fusion; default `20` |
| `VECTOR_TOP_K` | No | Vector candidate count in hybrid mode; default `20` |
| `HYBRID_RRF_K` | No | RRF rank constant; default `60` |
| `HYBRID_FINAL_TOP_K` | No | Fused candidate count before MMR/rerank; default `8` |
| `HYBRID_MIN_BM25_SCORE` | No | Minimum BM25 score included in fusion; default `0.0` |
| `HYBRID_FAIL_OPEN` | No | On BM25 failure, continue vector-only when `true`; default `true` |
| `RERANKER_BACKEND` | No | `heuristic` (default), `cohere`, or `cross_encoder` (requires `sentence-transformers` in the image) |
| `COHERE_API_KEY` | When cohere reranker | Cohere Rerank API key |
| `COHERE_RERANK_MODEL` | No | Default `rerank-english-v3.0` |
| `CROSS_ENCODER_MODEL` | No | Default `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| `QUERY_REWRITE_ENABLED` | No | LLM query rewrite before retrieval; default `false` |
| `ENABLE_OTEL` | No | OpenTelemetry tracing; default `false` (install OTEL packages separately — see `docs/PRODUCTION.md`) |
| `OTLP_ENDPOINT` | When OTel enabled | OTLP HTTP trace exporter URL |

See `.env.example` for full list.

## DevOps quick start

**Start here:** **[docs/DEVOPS_DEPLOY.md](docs/DEVOPS_DEPLOY.md)**

```bash
# On EC2 (one-time)
cp .env.devops.example .env.production   # fill OPENAI_API_KEY, APIFY_API_TOKEN
docker compose -f docker-compose.ec2.yml --env-file .env.production up -d

# Verify
API_URL=https://devapi-chatbot.mobcoder.ai ./scripts/aws/smoke_health.sh

# Refresh knowledge on server
./scripts/ec2/reingest.sh
```

| Artifact | Purpose |
|----------|---------|
| `docs/DEVOPS_DEPLOY.md` | **Primary** DevOps handoff |
| `.env.devops.example` | Canonical EC2 env template |
| `scripts/ec2/reingest.sh` | Crawl + ingest on EC2 |
| `scripts/aws/deploy_widget_dev.sh` | Widget to S3/CloudFront |

## AWS production (API only)

### EC2 (single instance — recommended for simpler deploys)

One EC2 box runs Postgres + Redis + API via Docker Compose. GitHub Actions builds the image, pushes to ECR, and deploys over SSH.

Guide: **[docs/DEVOPS_DEPLOY.md](docs/DEVOPS_DEPLOY.md)** (primary) · **[docs/EC2.md](docs/EC2.md)** (instance detail)

| Artifact | Purpose |
|----------|---------|
| `docker-compose.ec2.yml` | EC2 stack (Postgres, Redis, API on port 80) |
| `.env.devops.example` | **Canonical** server env → copy to `.env.production` on EC2 |
| `.env.ec2.example` | Equivalent to `.env.devops.example` |
| `scripts/ec2/setup_ec2.sh` | One-time Docker bootstrap on the instance |
| `scripts/ec2/deploy.sh` | Pull ECR image + `docker compose up` |
| `scripts/ec2/reingest.sh` | Crawl + ingest knowledge on EC2 |
| `.github/workflows/deploy-ec2.yml` | CI/CD on push to `main` |

GitHub secrets: `AWS_REGION`, `EC2_HOST`, `EC2_USER`, `EC2_SSH_PRIVATE_KEY`, plus ECR push creds (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` or `AWS_DEPLOY_ROLE_ARN`).

### ECS Fargate (managed, multi-AZ)

Deploy with **Terraform** — full stack in one apply. Guide: **[docs/AWS.md](docs/AWS.md)** and **[deploy/terraform/README.md](deploy/terraform/README.md)**

```bash
cd deploy/terraform && cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

| Artifact | Purpose |
|----------|---------|
| `deploy/terraform/` | VPC, RDS, Redis, EFS, ECS, ALB, secrets |
| `.env.aws.example` | Production env reference |
| `scripts/aws/deploy_api.sh` | Push image + roll out ECS |
| `.github/workflows/deploy-aws.yml` | ECS deploy on `main` |

### CI/CD (all workflows)

| Workflow | Trigger |
|----------|---------|
| `ci.yml` | Tests on every push/PR |
| `deploy-ec2.yml` | EC2 deploy on `main` |
| `deploy-aws.yml` | ECS deploy on `main` |
| `weekly-crawl.yml` | Scheduled knowledge refresh |

Widget local demo:

```bash
cd widget && python -m http.server 8765
# http://127.0.0.1:8765/demo.html
```

## Docker

**Local (Postgres + Redis + API):**

```bash
docker compose up --build
```

- API: http://127.0.0.1:8001/api/v1/health  
- Postgres: `localhost:5432` (`mobcoder` / `mobcoder` / `mobcoder`)

**Production-like (Postgres + Redis + API + nginx widget proxy):**

```bash
cp .env.production.example .env.production   # fill OPENAI_API_KEY
docker compose -f docker-compose.prod.yml --env-file .env.production up --build -d
```

- API: http://127.0.0.1:8001/api/v1/health  
- Widget: http://127.0.0.1:8765/demo.html

First boot auto-ingests seed knowledge when Chroma is empty (`AUTO_INGEST_ON_START=true`).

**Staging (2 API replicas + shared Redis):**

```bash
cp .env.staging.example .env.staging   # fill secrets locally
docker compose -f docker-compose.staging.yml --env-file .env.staging up --build -d
python3 scripts/staging_smoke.py --live   # STAGING_API_URL=http://127.0.0.1:8001
```

API on http://localhost:8001 (and http://localhost:8002 for replica 2). Production uses named volume `app_data`; local dev mounts `./data`.

## Tests

```bash
pytest tests/ -q
```

## TODO

- [ ] **Admin portal v1** — extend MVP at `/admin` with session inbox, conversation detail, CRM dispatch log, and analytics summary. MVP today: feedback stats, feedback list, leads list. See **[docs/ADMIN_PORTAL.md](docs/ADMIN_PORTAL.md)**. Until then, use `/admin`, `python scripts/inspect_db.py`, `python scripts/inspect_crm.py`, or pgAdmin.
- [ ] **Set `INTERNAL_API_KEY` in production** — without it, `/admin` data loads, `GET /feedback*`, and `GET /leads` return `404`.
- [ ] **Embed widget on live mobcoder.ai** — see `widget/embed-snippet.html` and **[docs/WEBSITE_BOT.md](docs/WEBSITE_BOT.md)**.

## Repository

https://github.com/pranaya-mathur/help-assistant
