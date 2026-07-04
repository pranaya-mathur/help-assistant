# Website bot rollout (help visitors + capture leads)

This guide covers deploying the **Mobcoder AI website assistant** on [mobcoder.ai](https://mobcoder.ai): answer questions from site content, guide visitors, and capture name/email (plus sales fields when intent is strong).

## What you are shipping

| Piece | Role |
|-------|------|
| `widget/mobcoder-chat.js` | FAB + chat panel embedded on every page |
| FastAPI (`/api/v1/chat`) | RAG answers + lead profiling |
| Postgres sessions | Stored leads, analytics, conversation turns |
| Calendly / contact URLs | Booking + contact CTAs |
| Google Sheets (optional) | Live lead log for leadership — `GOOGLE_SHEETS_WEBHOOK_URL` |
| Google Chat (optional) | One-time hot-lead alert — `GOOGLE_CHAT_WEBHOOK_URL` |

Not required for v1 launch: HubSpot, Apollo, full admin portal (minimal **`/admin`** MVP exists for feedback + leads). Google Sheets/Chat are optional interim visibility while CRM is wired. Ticket systems optional.

## 1. API (backend)

Ensure the API is running with at least:

```bash
OPENAI_API_KEY=...
SESSION_STORE_BACKEND=postgres
DATABASE_URL=postgresql://...
AUTO_MIGRATE_DB=true
PERSIST_ANALYTICS_EVENTS=true
ENABLE_LEAD_QUALIFICATION=true
ENABLE_LLM_GROUNDING=true
ENABLE_CHAT_STREAMING=true
CALENDLY_URL=https://calendly.com/hello-mobcoder/mobcoderai
CONTACT_PAGE_URL=https://mobcoder.ai/contact-us
CORS_ALLOWED_ORIGINS=https://mobcoder.ai,https://www.mobcoder.ai,https://devweb-agent.mobcoder.ai
# Optional — see docs/GOOGLE_SHEETS_LEAD_LOG.md and docs/GOOGLE_CHAT_LEAD_ALERTS.md
# GOOGLE_SHEETS_WEBHOOK_URL=
# GOOGLE_CHAT_WEBHOOK_URL=
```

**Pilot API:** `https://devapi-chatbot.mobcoder.ai`  
**Production API (when live):** `https://api.mobcoder.ai`

### Knowledge (answers quality)

```bash
python scripts/crawl_mobcoder.py --use-sitemap --max-pages 500
python scripts/ingest.py --reset-collection
curl -s https://YOUR-API/api/v1/health | python -m json.tool
# vector_store_count should be > 0
```

### Smoke test

```bash
python scripts/qa_website_bot.py --live --api-url https://devapi-chatbot.mobcoder.ai
```

## 2. Widget (frontend)

### Option A — Embed on mobcoder.ai (recommended)

Copy from [`widget/embed-snippet.html`](../widget/embed-snippet.html) into the site template before `</body>`.

**Pilot:** keep `MOBCODER_CHAT_API_URL` pointing at `devapi-chatbot`.  
**Production:** remove that line on `mobcoder.ai` — `mobcoder-chat.js` auto-detects `https://api.mobcoder.ai/api/v1/chat`.

Host `mobcoder-chat.js` on your CDN (or S3 behind CloudFront). Dev bucket example:

```bash
export WIDGET_S3_BUCKET=your-bucket AWS_REGION=us-east-1
./scripts/aws/deploy_widget_dev.sh
```

### Option B — Dev full-page demo

`widget/demo.html` → S3 as `index.html` for `https://devweb-agent.mobcoder.ai` (GitLab CI on `dev` branch).

## 3. Lead capture flow

1. Visitor chats → bot answers from crawled mobcoder.ai content.
2. After rapport, bot may ask for **name + email** (help) or **project → timeline → budget → contact** (sales).
3. Widget qualify form saves `lead_profile` to localStorage + server session.
4. Leads visible in Postgres:

```bash
python scripts/inspect_db.py
```

5. Optional: set `HUBSPOT_WEBHOOK_URL` to push qualified leads to CRM.

## 4. Pre-launch checklist

- [ ] `GET /api/v1/health` → `vector_store_count > 0`
- [ ] `python scripts/qa_website_bot.py --live` passes
- [ ] CORS allows `https://mobcoder.ai` and `https://www.mobcoder.ai`
- [ ] Widget loads on mobile + desktop
- [ ] “Book a call” → Calendly; “Contact us” → `/contact-us`
- [ ] Test path: question → answer → share name/email → see session in DB
- [ ] Proactive popups suppressed on `/contact-us` and after lead captured

## 6. Context-aware behavior

The bot uses **exact page context** (not just page type):

| Signal | Source | Effect |
|--------|--------|--------|
| `page_url` | Widget on every chat request | RAG boost + prompt |
| `page_title` | `document.title` from host page | Generic queries ("hi", "tell me more") anchor to current page |
| `GET /api/v1/widget-context` | Widget on first open | Backend-driven opener + starter chips |
| UTM / referrer / scroll | `visitor_meta` + session | Tone and closing CTA hints in prompt (no invented facts) |
| Page journey | `first_page_url` / `last_page_url` in session | Bridge chips when visitor navigates (e.g. case study → pricing) |

Config: `PAGE_CONTEXT_BOOST_ENABLED=true` (default). Set `false` to disable URL-priority retrieval without redeploying the widget.

`LLM_SUGGESTED_REPLIES_ENABLED=true` (default) adds one small JSON LLM call per turn for dynamic follow-up chips (max 4 total after merge with heuristics). Set `false` to save cost.

### Interactivity (chips, cards, proactive)

| Feature | Behavior |
|---------|----------|
| Follow-up chips | Up to **4** per reply — heuristic qualify/booking chips first, then optional LLM chips |
| Qualify choices | Sales intent with project need but no timeline/budget → one-tap timeline/budget chips |
| Citation cards | Case-study pages show title + snippet cards (max 2); other pages keep compact links |
| Proactive popup | On `ai_agents`, `services`, `case_studies`, `about` (not contact). `pricing` removed — `/pricing` 404s on the live site post-redesign. |

### SPA navigation (mobcoder.ai)

If the site is a single-page app, update context when the route changes:

```html
<script>
  // Always keep page URL in sync for chat requests
  window.MOBCODER_PAGE_URL = location.href;

  // On route change — refresh opener + starter chips if panel is open and visitor has not sent a message yet
  function onRouteChange() {
    window.MOBCODER_PAGE_URL = location.href;
    if (window.MobcoderChat && typeof window.MobcoderChat.onPageChange === "function") {
      window.MobcoderChat.onPageChange(location.href);
    }
  }
</script>
```

Wire `onRouteChange()` from your router (Next.js `router.events`, React Router `useLocation`, etc.).

### Success criteria (manual)

| Scenario | Expected |
|----------|----------|
| `"hi"` on a case-study URL | Answer references that page; citation URL matches path when indexed |
| Widget open on pricing page | Opener/chips match `GET /widget-context?page_url=.../pricing` |
| UTM `utm_campaign=ai` | Present in server prompt; no fabricated campaign claims in reply |
| Help query with RAG hit | `suggested_replies` length ≤ 4 and non-empty |
| Case-study citation | API `citations[].snippet` populated; widget shows card layout on case-study URLs |
| SPA route change (no messages yet) | `MobcoderChat.onPageChange(url)` refreshes greeting + starter chips |

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| CORS error in browser | Add site origin to `CORS_ALLOWED_ORIGINS` on API |
| Empty / generic answers | Re-run crawl + ingest; check health `vector_store_count` |
| Widget calls wrong API | Set `window.MOBCODER_CHAT_API_URL` explicitly in embed snippet |
| Leads not in DB | Confirm `SESSION_STORE_BACKEND=postgres` and `DATABASE_URL` on API |
| Old dev widget | Re-upload `demo.html` / invalidate CloudFront (`docs/DEV_WIDGET_DEPLOY.md`) |
