# Mobcoder AI Chat Widget

Embeddable chat widget (`mobcoder-chat.js`) for **website visitors**: answer questions from mobcoder.ai content and capture lead details.

Full rollout guide: **[docs/WEBSITE_BOT.md](../docs/WEBSITE_BOT.md)**

## Run locally

**Terminal 1 — API:**

```bash
python main.py
```

**Terminal 2 — widget:**

```bash
cd widget
python -m http.server 8765
```

Open **http://127.0.0.1:8765/demo.html** (full-page demo) or **contact-us.html** / **pricing.html** (embed widget on a simulated page).

Ensure API `CORS_ALLOWED_ORIGINS` includes `http://127.0.0.1:8765` and `http://localhost:8765`.

## Embed on mobcoder.ai (production)

Use **`mobcoder-chat.js`** (FAB + panel). Copy-paste from **`embed-snippet.html`**.

**Pilot** (API on devapi-chatbot):

```html
<script>
  window.MOBCODER_CHAT_API_URL = "https://devapi-chatbot.mobcoder.ai/api/v1/chat";
  window.MOBCODER_CALENDLY_URL = "https://calendly.com/hello-mobcoder/mobcoderai";
  window.MOBCODER_CONTACT_URL  = "https://mobcoder.ai/contact-us";
  window.MOBCODER_COMPANY_NAME = "Mobcoder AI";
  window.MOBCODER_AGENT_NAME   = "Mobcoder AI Assistant";
  window.MOBCODER_CHAT_STREAM  = true;
  window.MOBCODER_AUTO_OPEN_DELAY_SECONDS = 45;
</script>
<script src="https://YOUR-CDN/mobcoder-chat.js" defer></script>
```

**Production** (when `api.mobcoder.ai` is live): omit `MOBCODER_CHAT_API_URL` on `mobcoder.ai` — the script auto-detects the API host.

## API auto-detection (`mobcoder-chat.js`)

| Page hostname | API (if `MOBCODER_CHAT_API_URL` unset) |
|---------------|----------------------------------------|
| `localhost` / `127.0.0.1` | `http://127.0.0.1:8001` |
| `devweb-agent.mobcoder.ai` | `https://devapi-chatbot.mobcoder.ai` |
| `mobcoder.ai` / `www.mobcoder.ai` | `https://api.mobcoder.ai` |

Override anytime with `window.MOBCODER_CHAT_API_URL`.

## Booking vs contact CTAs

| CTA | Destination |
|-----|-------------|
| Book a Call / discovery booking | `https://calendly.com/hello-mobcoder/mobcoderai` |
| Contact Us chip | `https://mobcoder.ai/contact-us` |

## Proactive behavior

- Auto-open and exit-intent are suppressed when a lead (name + email) is known or after qualification.
- `/contact-us` pages skip proactive auto-open and exit-intent (site already has form + calendar).
- Book banner dismiss is persisted in `localStorage` (`mc_dismiss_book_banner`).

## Two widget surfaces

| File | Use |
|------|-----|
| `demo.html` | Full-page pilot UI (deployed to dev S3 as `index.html`) |
| `mobcoder-chat.js` | **Production embed** for mobcoder.ai (FAB + panel) |
| `embed-snippet.html` | Copy-paste HTML for site template |

`contact-us.html` and `pricing.html` load `mobcoder-chat.js` against local API for embed QA.

## QA

```bash
python scripts/qa_website_bot.py --live --api-url https://devapi-chatbot.mobcoder.ai
```

## Fresh session

Clear `localStorage` keys `mc_session_id`, `mc_lead_profile`, `mc_proactive_suppressed` or use incognito.
