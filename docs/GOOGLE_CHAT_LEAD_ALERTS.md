# Google Chat — human-review lead alerts

When a hot, contactable lead crosses the human-review threshold, the backend can
POST a one-time alert to a Google Chat space. This gives sales immediate visibility
while HubSpot CRM wiring is still pending.

Pair with **[GOOGLE_SHEETS_LEAD_LOG.md](GOOGLE_SHEETS_LEAD_LOG.md)** for a live
lead spreadsheet; Chat is for real-time pings on high-value leads only.

## How it works

```
chat turn → post-response enrichment (background thread)
         → persist_qualification()
         → needs_human_review + lead_consent?
         → app/integrations/google_chat.py (once per session)
```

- Alerts fire only when **`needs_human_review`** is true **and** the visitor gave
  **`lead_consent`** (same gate as CRM dispatch and the Sheets lead log).
- Each session sends **at most one** alert (`human_review_notified` flag in session
  metadata prevents repeat pings on every turn).
- Not configured (`GOOGLE_CHAT_WEBHOOK_URL` unset) → silently disabled.

### When is a lead flagged for review?

`app/agent/routing.py` sets `needs_human_review` when all of the following hold:

- Lead profile includes an **email**
- Qualification bucket is **hot**
- Either **enterprise/compliance** signals appear in score reasons, or the
  **value** score component is ≥ 20 (mid budget band or better)

The alert body is built by `build_human_review_summary()` — name, email, company,
scores, project need, role/industry, and top scoring signals.

## Setup (~5 minutes)

### 1. Create a Google Chat space webhook

1. Open [Google Chat](https://chat.google.com/) and create or open a space (e.g.
   **Mobcoder AI — hot leads**).
2. Space name → **Apps & integrations** → **Manage webhooks**.
3. Add a webhook, copy the URL (`https://chat.googleapis.com/v1/spaces/…/messages?…`).

### 2. Configure the backend

```bash
GOOGLE_CHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/…/messages?key=…&token=…
```

Add to `.env` locally and to `.env.production` on EC2 (see `.env.devops.example`).
Terraform does not provision this URL yet — set it manually in the server env file.

### 3. Verify

1. Run a sales-mode chat where the visitor shares email + consent and triggers a
   hot score (budget, timeline, decision-maker language).
2. Confirm one card appears in the Chat space with session id and score summary.
3. Send another message in the same session — **no second alert** (expected).

## Payload shape

The integration POSTs JSON with a plain-text fallback and a `cardsV2` card:

- Header: bucket emoji + “Lead flagged for human review”
- Body: escaped summary lines from `human_review_summary`
- Footer: session id and `page_url` when present

Retries: up to 3 attempts with exponential backoff (`httpx`, 8s timeout).

## Related code

| File | Role |
|------|------|
| `app/agent/routing.py` | `needs_human_review`, `build_human_review_summary` |
| `app/agent/post_response.py` | `_notify_human_review_once`, consent gate |
| `app/integrations/google_chat.py` | Webhook POST |
| `app/config/settings.py` | `GOOGLE_CHAT_WEBHOOK_URL` |

## See also

- [GOOGLE_SHEETS_LEAD_LOG.md](GOOGLE_SHEETS_LEAD_LOG.md) — upsert all consenting leads to a sheet
- [PRODUCTION.md](PRODUCTION.md) — CRM payload, consent, HubSpot
- [README.md](../README.md) — architecture and env vars
