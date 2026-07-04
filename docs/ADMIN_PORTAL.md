# MobCoder Sales Agent — Admin Portal

**Status:** Partial — MVP shipped at `/admin`  
**Audience:** MobCoder sales, marketing, and operations  
**Last updated:** July 2026

---

## What this is

The Admin Portal is an internal website where the MobCoder team can see what the sales chatbot is doing in the real world: who chatted, what they asked, whether they became a lead, and whether that lead reached HubSpot.

Today, all of that information already lives in our PostgreSQL database. A **minimal read-only MVP** is live at **`GET /admin`** (static UI in `app/static/admin.html`). It requires `INTERNAL_API_KEY` on the API calls it makes (`X-Internal-Key` header). The rest of this document describes the **full v1 scope** still to build on top of that MVP.

---

## What exists today (MVP)

| Surface | What it shows |
|---------|----------------|
| **`/admin`** | Feedback stats (totals, score), recent feedback rows, lead list (name/email/company) |
| **`GET /api/v1/feedback`** | Paginated feedback records (internal API) |
| **`GET /api/v1/feedback/stats`** | Aggregate thumbs totals (internal API) |
| **`GET /api/v1/leads`** | Sessions with captured name or email (internal API) |

**Not in the MVP yet:** session inbox with filters, full conversation threads, CRM dispatch log, analytics dashboard, per-session detail page, CRM block, attribution panel.

Set `INTERNAL_API_KEY` in `.env` / production env, then open `http://127.0.0.1:8001/admin` (or your API base + `/admin`).

---

## The problem we are solving

When someone uses the chat widget on mobcoder.ai (or our local demo), the backend quietly records a lot of useful context:

- The conversation itself (questions and answers)
- Lead details when the visitor shares them (name, email, company, project need)
- Marketing attribution (which page they were on, UTM tags, referrer)
- Technical signals (timezone, language, client IP — captured server-side)
- Whether the bot considered them qualified enough to send to CRM
- Whether the HubSpot webhook succeeded or failed
- Thumbs-up / thumbs-down feedback on individual answers

That data powers the product, but right now there is no friendly place to review it. Sales and ops either ask engineering to run a query, or they squint at raw JSON in pgAdmin. Neither scales.

We need a single internal URL where an authorised teammate can answer questions like:

- “Did anyone from Acme Corp chat with us this week?”
- “Why did this lead not show up in HubSpot?”
- “Which pages drive the most qualified conversations?”
- “What answers are visitors marking as unhelpful?”

---

## Who will use it

| Role | Typical questions |
|------|-------------------|
| **Sales** | Who qualified yesterday? What did they ask? Did CRM get the lead? |
| **Marketing** | Which UTM campaigns bring engaged visitors? Which landing pages convert? |
| **Support / ops** | Is the bot giving bad answers? Any escalation requests? |
| **Engineering** | Quick sanity check without opening pgAdmin (secondary user) |

The portal is **internal only**. It is not a customer-facing product and it is not a replacement for HubSpot’s own CRM UI.

---

## What data exists today (behind the scenes)

When `SESSION_STORE_BACKEND=postgres` is enabled, the API writes to these tables:

| Table | What it holds |
|-------|----------------|
| `chat_sessions` | One row per visitor session: lead profile, full conversation JSON, intent/stage, and a `metadata` blob (IP, UTM, page URLs, CRM flags) |
| `crm_dispatches` | Each attempt to send a qualified lead to HubSpot: status, payload, error message |
| `analytics_events` | Funnel events (`widget_opened`, `message_sent`, `lead_qualified`, `crm_dispatch_failed`, etc.) |
| `conversation_turns` | Normalised message history (user/assistant turns per session) |
| `feedback` | Thumbs ratings tied to a session and message |

Some of this is already exposed through internal API endpoints (for example `GET /api/v1/feedback` with an `X-Internal-Key` header). The portal will sit on top of the same database and patterns — not invent a parallel data store.

---

## What the portal should do (version 1)

Version 1 is **read-only**. Nobody edits or deletes sessions from the UI. The goal is visibility and trust, not administration.

### 1. Session inbox

A searchable list of recent chat sessions, newest first. Each row should show enough to decide whether to open it: time, visitor email (if known), company, intent, stage, lead score label, page URL, and a simple CRM status badge (sent / pending / failed / none).

Filters worth supporting on day one:

- Date range
- Has email (yes / no)
- Intent (`help`, `sales`, `booking`, etc.)
- CRM dispatch status
- Free-text search on email or company

Clicking a session opens a detail view.

### 2. Session detail

The detail page is the heart of the product. On one screen, a sales person should see:

**Conversation** — the full thread, readable like a chat app (user messages on one side, bot on the other). No raw JSON.

**Lead profile** — name, email, company, project need, timeline, budget band, project type, and anything Apollo enrichment added.

**Attribution** — first and last page URL, referrer, UTM parameters, timezone, language, scroll depth (if captured), first/last client IP, and user-agent string.

**CRM block** — if a dispatch was attempted: timestamp, status, lead score, meeting readiness, conversation summary that was sent to HubSpot, and the error message if it failed. This alone would have saved us hours on “why isn’t my test lead in HubSpot?”

**Feedback** — any thumbs-up/down left on bot messages in this session, with the actual question and answer shown inline.

### 3. CRM dispatch log

A dedicated view of the `crm_dispatches` table — essentially a queue audit trail. Sort by status so failures float to the top. Each row links back to the parent session.

This is especially important in staging and early production, when `HUBSPOT_WEBHOOK_URL` misconfiguration shows up as `failed` rows with a clear error string.

### 4. Analytics summary

A simple dashboard, not a full BI tool. For a selected date range, show counts of key events:

- Widget opened
- Messages sent
- Leads qualified
- CRM dispatches (queued / success / failed)
- Booking CTA clicks
- Human escalation requests

Optional simple breakdown by `page_category` or UTM source if the query stays fast. We can add charts later; v1 can be numbers in a table.

### 5. Feedback review

Reuse and extend the existing feedback list API. Show recent negative feedback prominently — product and prompt quality depend on this loop.

---

## How someone would use it (example flows)

**Monday morning, sales stand-up**  
Open the portal → filter “last 7 days” + “has email” → scan qualified sessions → click into any that look interesting → confirm CRM status is `success` before calling the prospect.

**Marketing campaign review**  
Filter sessions where `utm_source` matches the campaign → compare `widget_opened` vs `lead_qualified` in the analytics tab → adjust landing page copy based on common questions in session detail.

**Debugging a missing HubSpot lead**  
Search by email → open session → CRM block shows `failed` with “HubSpot webhook failed after retries” → engineering checks `HUBSPOT_WEBHOOK_URL`; sales knows the lead is still in our DB.

**Improving the bot**  
Open feedback view → sort by negative → read the exact user question and bot answer → file a prompt or knowledge-base fix.

---

## Access and security

The portal must never be public.

For version 1, we can align with what the API already does for internal routes:

- Protect all admin routes with `INTERNAL_API_KEY` (sent as `X-Internal-Key` on API calls, or equivalent session login if we add a thin auth layer in front)
- Host on an internal URL or behind VPN / IP allowlist in production
- Read-only database access from the portal backend — no `UPDATE` or `DELETE` on visitor data
- No bulk CSV export of PII in v1 (reduces accidental data leakage)
- Audit log of who opened the portal (nice-to-have; can follow in v2)

Visitor messages and lead emails are personal data. Treat this like any internal CRM viewer: least privilege, MobCoder staff only.

---

## What we are not building in v1

To keep the first release small and shippable:

- **Not a HubSpot replacement** — we display dispatch status; editing deals stays in HubSpot.
- **Not live chat takeover** — no agent jumping into an active widget session.
- **Not session editing** — no manual correction of lead emails or deletion of conversations from the UI.
- **Not a public analytics embed** — internal eyes only.
- **Not complex role-based permissions** — single shared internal key or one login tier is enough to start.

These can be revisited once v1 is in daily use.

---

## What to use for data not yet in the MVP

For sessions, CRM dispatches, analytics events, and full conversation JSON, use:

```bash
python scripts/inspect_db.py      # sessions, metadata, recent events
python scripts/inspect_crm.py     # CRM dispatch rows and status
```

Or connect with **pgAdmin** to the `mobcoder` database on `localhost:5432` (see the main README, PostgreSQL section, for connection details).

Example SQL for a quick session + CRM check:

```sql
SELECT
  s.session_id,
  s.updated_at,
  s.lead_profile->>'email' AS email,
  s.metadata->>'first_client_ip' AS ip,
  d.status AS crm_status,
  d.error_message
FROM chat_sessions s
LEFT JOIN crm_dispatches d ON d.session_id = s.session_id
ORDER BY s.updated_at DESC
LIMIT 20;
```

---

## Suggested technical approach (for when we build it)

This section is guidance for implementation, not a commitment to a specific stack.

- **Backend:** Extend internal routes (`/api/v1/leads`, `/feedback*`, future `/api/v1/admin/*`) — all gated by `_require_internal_key` (same helper as feedback).
- **Frontend:** Extend `app/static/admin.html` or replace with a richer SPA. MobCoder branding, table-heavy layout, mobile-friendly enough for laptop use.
- **Data:** Direct Postgres reads via existing `app/infra/postgres.py` pool, or thin query helpers in `app/admin/` — no duplicate writes.
- **Deploy:** Same ECS service as the API (separate path) or a small second service on an internal ALB listener; never expose on the public widget origin.

Existing building blocks to reuse:

- **`/admin`** + `GET /api/v1/feedback`, `/feedback/stats`, `/leads` — MVP feedback and lead list
- `scripts/inspect_db.py` / `scripts/inspect_crm.py` — query patterns to port into API endpoints
- `chat_sessions.metadata` JSON schema — documented in README visitor attribution table

---

## Success criteria for v1

We will know v1 is successful when:

1. A sales teammate can find yesterday’s qualified leads without asking engineering.
2. A failed CRM dispatch is diagnosable in under two minutes from the session detail page.
3. Marketing can tie at least one campaign’s UTM tag to real conversations.
4. Negative feedback is reviewable in one click from the portal home.

---

## Open questions (to decide before build)

- Single shared password vs per-user accounts?
- Hosted path: `/admin` on the API domain vs separate `admin.mobcoder.internal`?
- Do we need email alerts when CRM dispatch fails, or is the portal enough?
- Retention: portal shows same 30-day session TTL as `prune_sessions.py`, or longer archive?

---

## Related docs

- [README.md](../README.md) — local Postgres setup, visitor attribution fields
- [PRODUCTION.md](PRODUCTION.md) — Postgres, CRM, HubSpot webhook configuration
- [widget/README.md](../widget/README.md) — what the widget sends (`visitor_meta`, events)

---

**TODO:** Extend the MVP at `/admin` to full v1 scope (session inbox, detail view, CRM log, analytics summary).
