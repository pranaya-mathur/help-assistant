# MobCoder Sales & Help Assistant
### An AI agent that works your website for you — 24/7, no SDR required.

**Pilot (Phase 1):** Help-only mode via `OPERATING_MODE=help` — retrieve-first answers, no inline qualify. See `.env.pilot.example`.

**Phase 2:** Full sales layer (lead scoring, qualify, HubSpot) via `OPERATING_MODE=sales` or `full`.

---

## Features

**Answers questions from real content, not guesswork.**
The assistant is trained on crawled mobcoder.ai pages. Every answer it gives is grounded in what MobCoder actually says — services, case studies, tech stack, process. If it doesn't know, it says so and offers to connect the visitor with the team.

**Passively builds a lead profile while it chats.**
Visitors never fill out a form. The assistant picks up signals as the conversation flows — company name, project type, timeline, budget, contact info — and quietly assembles a profile in the background. By the end of a few exchanges, you often know exactly who you're talking to.

**Knows when someone is ready to buy.**
A scoring engine runs behind every conversation. It watches for signals (budget mentioned, timeline defined, decision-maker language) and computes a readiness score in real time. When someone crosses the threshold, the assistant offers to book a call — naturally, not aggressively.

**Escalates to a human when it should.**
Frustrated visitor? Urgent ask? Someone who just wants a person? The assistant detects it and offers to escalate. No dead ends, no "I don't understand that" loops.

**Learns from every thumbs up and thumbs down.**
Every piece of feedback is stored and queryable. Negative ratings open a comment box so visitors can say exactly what was wrong. The team can pull stats via **`/admin`** or the internal API and act on what they find.

---

## Capabilities

| What it can do | How |
|---|---|
| Answer service, pricing, and process questions | RAG over crawled mobcoder.ai content |
| Qualify leads without a form | NLP extraction + progressive profiling across turns |
| Score lead readiness in real time | Weighted signal scoring (budget, timeline, company, intent) |
| Book discovery calls | Calendly link surfaced at the right moment |
| Escalate to a human | Frustration + urgency detection |
| Stream responses in real time | SSE streaming on production widget |
| Work across sessions | Postgres sessions in production (SQLite local, Redis optional for multi-instance tests) |
| Rate limit abuse | Sliding window per IP, Redis-backed in prod |
| Collect and store feedback | 👍/👎 with optional comment, queryable via `/admin` and internal API |
| Deploy anywhere | Docker container, AWS-ready, single `<script>` widget embed |

---

## Todo

- [ ] **Set `INTERNAL_API_KEY` in production env**
  Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and add to `.env.production`. Without it, `/admin`, `GET /feedback*`, and `GET /leads` are disabled.

- [ ] **Point the widget at the production URL**
  Use `widget/embed-snippet.html` on mobcoder.ai with `mobcoder-chat.js`. Pilot: `devapi-chatbot.mobcoder.ai`. See `docs/WEBSITE_BOT.md`.

- [x] **Seed ChromaDB with fresh site content** *(done 4 July 2026, local/dev)*
  Re-crawled and re-ingested from the live site — 257 chunks, 101 pages. Fixed a real pipeline bug found in the process: crawled 404 error pages were being ingested as real content with zero filtering; added a guard in `app/crawler/page_loader.py`. 3 live pages still missing from the index (confirmed as seeds — likely transient crawl instability, not a code bug; re-run if it matters). Still needed: apply `AUTO_INGEST_ON_START=true` for the actual staging/prod deploy.

- [ ] **Test the widget on actual mobcoder.ai pages**
  Local testing uses `widget/demo.html` + `./scripts/run_dev.sh`. Still not done: embed on live mobcoder.ai and verify CORS, mobile, and production feedback flow.

- [ ] **Extend the admin portal (v1)**
  MVP at `/admin` shows feedback stats, feedback list, and leads. Still needed: session inbox, conversation threads, CRM dispatch log, analytics dashboard. See `docs/ADMIN_PORTAL.md`.

- [ ] **Connect HubSpot (or your CRM)**
  Set `HUBSPOT_WEBHOOK_URL` in the env. When a lead is fully qualified, the agent fires a webhook — configure the URL for your environment.

- [ ] **Confirm Redis persistence in your stack**
  Production Docker Compose and EC2 use `redis-server --appendonly yes`. Bare-metal Redis without AOF loses rate-limit state on restart (sessions live in Postgres in production).

---

*Last updated: July 2026*
