/**
 * Mobcoder AI Embeddable Chat Widget v5
 * ─────────────────────────────────────────────────────────────────────────────
 * Config (set on window before loading this script):
 *   window.MOBCODER_CHAT_API_URL    – base chat endpoint (auto-detected on mobcoder.ai / dev host if omitted)
 *   window.MOBCODER_EVENTS_URL      – analytics endpoint  (auto-derived)
 *   window.MOBCODER_CALENDLY_URL    – discovery call booking (default: hello-mobcoder/mobcoderai)
 *   window.MOBCODER_CONTACT_URL   – contact form page (default: https://mobcoder.ai/contact-us)
 *   window.MOBCODER_CHAT_STREAM     – true | false        (default: true)
 *   window.MOBCODER_PRIMARY_COLOR   – hex accent colour   (default: #2563eb)
 *   window.MOBCODER_COMPANY_NAME    – company label       (default: Mobcoder AI)
 *   window.MOBCODER_AGENT_NAME      – agent display name  (default: Mobcoder AI Assistant)
 *   window.MOBCODER_AVATAR_URL      – agent avatar URL    (optional)
 *   window.MOBCODER_AUTO_OPEN_DELAY_SECONDS – proactive auto-open delay (default 45, 0=off)
 *   window.MOBCODER_SHOW_DEBUG_SALES_STATE – true | false (default: false)
 *   window.MOBCODER_PRIVACY_COPY    – lead-form consent text (optional)
 *   window.MOBCODER_PAGE_URL        – simulate production page URL when testing locally
 *                                     (e.g. https://mobcoder.ai/contact-us)
 *
 * v4 additions:
 *   - Response feedback (👍 / 👎) with POST /api/v1/feedback
 *   - Exit-intent capture (mouseleave viewport top) — one-time per session
 *   - Proactive trigger with per-category personalised message
 *
 * v5 additions (enterprise UI refresh):
 *   - Design-token CSS system scoped to #mc-root (no host-page :root pollution)
 *   - Expand / collapse (wide mode) toggle in the header
 *   - Streaming caret, message entrance animations, refined micro-interactions
 *   - Accessibility: focus-visible rings, ARIA labels/roles, prefers-reduced-motion
 */
(function () {
  "use strict";

  /** Resolve chat API from hostname when MOBCODER_CHAT_API_URL is not set. */
  function _defaultApiChatUrl() {
    var host = (window.location.hostname || "").toLowerCase();
    if (/^(localhost|127\.0\.0\.1)$/.test(host)) {
      return "http://127.0.0.1:8001/api/v1/chat";
    }
    if (host === "devweb-agent.mobcoder.ai") {
      return "https://devapi-chatbot.mobcoder.ai/api/v1/chat";
    }
    if (host === "mobcoder.ai" || host === "www.mobcoder.ai") {
      // Override with window.MOBCODER_CHAT_API_URL during pilot if production API is not live yet.
      return "https://api.mobcoder.ai/api/v1/chat";
    }
    return "http://127.0.0.1:8001/api/v1/chat";
  }

  const API_URL      = window.MOBCODER_CHAT_API_URL  || _defaultApiChatUrl();
  const STREAM_URL   = window.MOBCODER_CHAT_STREAM_URL || API_URL.replace(/\/chat\/?$/, "/chat/stream");
  const EVENTS_URL   = window.MOBCODER_EVENTS_URL    || API_URL.replace(/\/chat\/?$/, "/events");
  const ESCALATE_URL = window.MOBCODER_ESCALATE_URL  || API_URL.replace(/\/chat\/?$/, "/escalate");
  const FEEDBACK_URL = window.MOBCODER_FEEDBACK_URL  || API_URL.replace(/\/chat\/?$/, "/feedback");
  const WIDGET_CONTEXT_URL = window.MOBCODER_WIDGET_CONTEXT_URL || API_URL.replace(/\/chat\/?$/, "/widget-context");
  const CALENDLY_URL = window.MOBCODER_CALENDLY_URL  || "https://calendly.com/hello-mobcoder/mobcoderai";
  const CONTACT_URL  = (window.MOBCODER_CONTACT_URL || "https://mobcoder.ai/contact-us").trim();
  const BOOKING_URL  = CALENDLY_URL;
  const AUTO_OPEN_DELAY = window.MOBCODER_AUTO_OPEN_DELAY_SECONDS !== undefined
    ? Number(window.MOBCODER_AUTO_OPEN_DELAY_SECONDS)
    : 45;
  // Default streaming to true — gives instant perceived response via SSE tokens.
  // Override: set window.MOBCODER_CHAT_STREAM = false before loading this script.
  const USE_STREAM   = window.MOBCODER_CHAT_STREAM !== false;
  const PRIMARY      = window.MOBCODER_PRIMARY_COLOR || "#2563eb";
  const COMPANY      = window.MOBCODER_COMPANY_NAME  || "Mobcoder AI";
  const AGENT_NAME   = window.MOBCODER_AGENT_NAME    || "Mobcoder AI Assistant";
  const AVATAR_URL   = window.MOBCODER_AVATAR_URL    || null;
  const CONFIG       = window.MOBCODER_CHAT_CONFIG || {};
  const SHOW_DEBUG_SALES_STATE =
    window.MOBCODER_SHOW_DEBUG_SALES_STATE === true ||
    CONFIG.showDebugSalesState === true;
  const PRIVACY_COPY = window.MOBCODER_PRIVACY_COPY || CONFIG.privacyCopy ||
    "By sharing your details, you agree that " + COMPANY + " may contact you about your inquiry. Please avoid sharing sensitive personal information in chat.";

  const STORAGE_SESSION = "mc_session_id";
  const STORAGE_LEAD    = "mc_lead_profile";
  const STORAGE_DISMISS_BOOK = "mc_dismiss_book_banner";
  const WIDGET_SEEN_KEY = "mc_widget_opened";
  const AUTO_OPEN_KEY   = "mc_auto_opened";
  const EXIT_INTENT_KEY = "mc_exit_intent_shown";  // session-scoped: one per session
  // "pricing" intentionally excluded: /pricing 404s on the live site post-redesign.
  const PROACTIVE_PAGES = ["ai_agents", "services", "case_studies", "about"];

  function _pageUrl() {
    var override = (window.MOBCODER_PAGE_URL || "").trim();
    return override || window.location.href;
  }

  function _pagePath() {
    var override = (window.MOBCODER_PAGE_URL || "").trim();
    if (override) {
      try {
        return new URL(override).pathname.toLowerCase();
      } catch (e) {}
    }
    return (window.location.pathname || "").toLowerCase();
  }

  function _pageTitle() {
    return String(document.title || "").substring(0, 200);
  }

  function fetchWidgetContext() {
    var q = "page_url=" + encodeURIComponent(_pageUrl()) + "&page_title=" + encodeURIComponent(_pageTitle());
    return fetch(WIDGET_CONTEXT_URL + "?" + q)
      .then(function(r) {
        if (!r.ok) throw new Error("widget context failed");
        return r.json();
      })
      .catch(function() {
        var cat = categoryFromPath();
        return {
          page_category: cat,
          opener: OPENERS[cat] || OPENERS.general,
          starter_chips: STARTER_CHIPS[cat] || STARTER_CHIPS.general,
        };
      });
  }

  function uuid() {
    if (crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      const r = Math.random() * 16 | 0;
      return (c === "x" ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }

  function getSessionId() {
    let id = localStorage.getItem(STORAGE_SESSION);
    if (!id) { id = uuid(); localStorage.setItem(STORAGE_SESSION, id); }
    return id;
  }

  function getLead() {
    try { return JSON.parse(localStorage.getItem(STORAGE_LEAD) || "{}"); } catch (e) { return {}; }
  }

  function saveLead(profile) {
    var existing = getLead();
    localStorage.setItem(STORAGE_LEAD, JSON.stringify(Object.assign({}, existing, profile)));
  }

  function hasKnownLead() {
    var lead = getLead();
    return !!(lead.email && lead.name);
  }

  function shouldSuppressProactive() {
    try {
      if (localStorage.getItem("mc_proactive_suppressed")) return true;
    } catch (e) {}
    return hasKnownLead();
  }

  function suppressProactive() {
    try { localStorage.setItem("mc_proactive_suppressed", "1"); } catch (e) {}
  }

  // ── Layer 1: Passive visitor fingerprint ──────────────────────────────────
  // Captured once on widget load, sent on every ChatRequest as visitor_meta.
  // Contains zero PII — only device/locale signals and marketing attribution.
  var _visitorMeta = null;

  function _parseUTMFromURL(url) {
    var out = {};
    try {
      var params = new URLSearchParams(new URL(url).search);
      ["utm_source","utm_medium","utm_campaign","utm_term","utm_content"].forEach(function(k) {
        var v = params.get(k);
        if (v) out[k] = v.substring(0, 256);
      });
    } catch (e) {}
    return out;
  }

  function _captureVisitorMeta() {
    if (_visitorMeta) return _visitorMeta;
    var utm = _parseUTMFromURL(_pageUrl());
    _visitorMeta = {
      timezone: (Intl && Intl.DateTimeFormat ? Intl.DateTimeFormat().resolvedOptions().timeZone : "") || "",
      language: (navigator.language || navigator.userLanguage || "").substring(0, 32),
      scroll_depth_pct: 0,               // updated on widget open, see below
      utm_source:   utm.utm_source   || "",
      utm_medium:   utm.utm_medium   || "",
      utm_campaign: utm.utm_campaign || "",
      utm_term:     utm.utm_term     || "",
      utm_content:  utm.utm_content  || "",
      referrer: (document.referrer || "").substring(0, 2000),
    };
    return _visitorMeta;
  }

  function _updateScrollDepth() {
    try {
      var scrolled = window.scrollY || document.documentElement.scrollTop || 0;
      var total = Math.max(
        document.body.scrollHeight - window.innerHeight,
        1
      );
      var pct = Math.min(100, Math.round((scrolled / total) * 100));
      var meta = _captureVisitorMeta();
      meta.scroll_depth_pct = Math.max(meta.scroll_depth_pct || 0, pct);
    } catch (e) {}
  }

  // Capture on load, update scroll depth lazily
  _captureVisitorMeta();
  window.addEventListener("scroll", _updateScrollDepth, { passive: true });
  // ─────────────────────────────────────────────────────────────────────────

  function esc(s) {
    return String(s || "")
      .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }

  function sanitizeBotMarkdown(text) {
    var t = String(text || "");
    t = t.replace(/\n+\*\*Sources\*\*[\s\S]*$/i, "");
    t = t.replace(/^\s*\*\*Sources\*\*\s*$/gim, "");
    return t.trim();
  }

  function dedupeCitationsList(cits) {
    var seen = {}, out = [];
    (cits || []).forEach(function (c) {
      var u = String(c.source_url || "").replace(/\/$/, "").toLowerCase();
      if (!u || seen[u]) return;
      seen[u] = true;
      out.push(c);
    });
    return out;
  }

  function renderBot(text) {
    return md(sanitizeBotMarkdown(text));
  }

  function md(text) {
    // Full markdown renderer for bot answers
    var lines = (text || "").split("\n");
    var out = [];
    var i = 0;
    var inList = false;  // ul
    var inOl = false;    // ol

    function flushList() {
      if (inList)  { out.push("</ul>"); inList = false; }
      if (inOl)    { out.push("</ol>"); inOl = false; }
    }

    function linkify(s) {
      // escape HTML first
      s = String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
      // markdown links
      s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        function(_,lbl,u){ return '<a href="'+u+'" target="_blank" rel="noopener noreferrer">'+lbl+'</a>'; });
      // bare URLs
      s = s.replace(/(^|[\s(])(https?:\/\/[^\s<>"')]+)/g,
        function(_,pre,u){ return pre+'<a href="'+u+'" target="_blank" rel="noopener noreferrer">'+u+'</a>'; });
      // inline formatting
      s = s.replace(/\*\*([^*\n]+)\*\*/g,"<strong>$1</strong>");
      s = s.replace(/\*([^*\n]+)\*/g,"<em>$1</em>");
      s = s.replace(/`([^`]+)`/g,'<code>$1</code>');
      return s;
    }

    while (i < lines.length) {
      var line = lines[i];

      // horizontal rule
      if (/^---+$/.test(line.trim())) {
        flushList();
        out.push("<hr>");
        i++; continue;
      }

      // headings ## / ###
      var hm = line.match(/^(#{1,3})\s+(.+)/);
      if (hm) {
        flushList();
        var lvl = Math.min(hm[1].length + 2, 5); // h3-h5 to not clash with page headings
        out.push("<h"+lvl+" class='mc-h'>"+linkify(hm[2])+"</h"+lvl+">");
        i++; continue;
      }

      // numbered list  1. item
      var olm = line.match(/^\d+\.\s+(.+)/);
      if (olm) {
        if (inList) { out.push("</ul>"); inList = false; }
        if (!inOl)  { out.push("<ol class='mc-ol'>"); inOl = true; }
        out.push("<li>"+linkify(olm[1])+"</li>");
        i++; continue;
      }

      // bullet list  - / * / •
      var ulm = line.match(/^[\-\*•]\s+(.+)/);
      if (ulm) {
        if (inOl) { out.push("</ol>"); inOl = false; }
        if (!inList) { out.push("<ul class='mc-ul'>"); inList = true; }
        out.push("<li>"+linkify(ulm[1])+"</li>");
        i++; continue;
      }

      // blank line -> paragraph break
      if (line.trim() === "") {
        flushList();
        out.push("<div class='mc-gap'></div>");
        i++; continue;
      }

      // blockquote
      var bqm = line.match(/^>\s+(.*)/);
      if (bqm) {
        flushList();
        out.push("<blockquote class='mc-bq'>"+linkify(bqm[1])+"</blockquote>");
        i++; continue;
      }

      // regular paragraph line
      flushList();
      out.push("<p class='mc-p'>"+linkify(line)+"</p>");
      i++;
    }
    flushList();
    return out.join("");
  }

  function categoryFromPath() {
    var p = _pagePath();
    if (/ai|agent|ml|genai|llm/.test(p))          return "ai_agents";
    if (/case-stud|portfolio|work|project/.test(p)) return "case_studies";
    if (/pricing|price/.test(p))                   return "pricing";
    if (/service|solution|product/.test(p))        return "services";
    if (/about|team|company/.test(p))              return "about";
    if (/contact|contact-us|get-in-touch/.test(p)) return "contact";
    if (/career|job|hiring/.test(p))               return "careers";
    if (/blog|insight|article/.test(p))            return "blog";
    return "general";
  }

  var OPENERS = {
    ai_agents:    "Hi! I'm "+AGENT_NAME+" 👋 You're exploring our AI & agent solutions — I can walk you through agentic systems, LLM pipelines, and production deployments. What would you like to know?",
    case_studies: "Hi! I'm "+AGENT_NAME+" 👋 I can walk you through "+COMPANY+" case studies and real-world projects. What industry or use-case interests you most?",
    services:     "Hi! I'm "+AGENT_NAME+" 👋 I can help with "+COMPANY+" services, engagement models, and how we tackle complex engineering challenges.",
    pricing:      "Hi! I'm "+AGENT_NAME+" 👋 I can explain "+COMPANY+"'s pricing approach — project-based, staff augmentation, and dedicated teams. For a custom quote, I can help you book a discovery call on our contact page.",
    about:        "Hi! I'm "+AGENT_NAME+" 👋 Ask me about the "+COMPANY+" team, culture, and how we partner with clients to build world-class products.",
    contact:      "Hi! I'm "+AGENT_NAME+" 👋 I can answer questions about "+COMPANY+" or help you schedule a discovery call with our team.",
    careers:      "Hi! I'm "+AGENT_NAME+" 👋 Interested in joining "+COMPANY+"? I can tell you about our culture, open roles, and what it's like to work here.",
    blog:         "Hi! I'm "+AGENT_NAME+" 👋 Exploring our insights? I can summarise articles or dive deeper into any topic from our blog.",
    general:      "Hi! I'm "+AGENT_NAME+" 👋 I'm here to help you learn about "+COMPANY+" — our services, AI solutions, or booking a call with our team.",
  };

  function openBookingPage(source) {
    track("booking_cta_clicked", source || "booking_link");
    window.open(BOOKING_URL, "_blank", "noopener,noreferrer");
  }

  function shouldOpenContactPage(msg) {
    if (!msg) return false;
    var m = String(msg).toLowerCase();
    return m.indexOf("discovery call") >= 0
      || m.indexOf("schedule a call") >= 0
      || m.indexOf("book a discovery") >= 0
      || m.indexOf("book a call") >= 0
      || m.indexOf("contact us for") >= 0
      || m.indexOf("tailored quote") >= 0
      || /^i'd like to (schedule|book)/i.test(msg);
  }

  var QUICK_CHIPS = [
    ["🤖 AI Services",  "What AI and agentic AI development services does Mobcoder AI offer?", ""],
    ["📁 Case Studies", "Can you share Mobcoder AI case studies and examples of AI projects you've delivered?", ""],
    ["💰 Pricing",      "How does Mobcoder AI approach pricing for custom software and AI projects?", ""],
    ["📅 Book a Call",  "", BOOKING_URL],
    ["📧 Contact Us",   "", CONTACT_URL],
  ];

  var STARTER_CHIPS = {
    ai_agents: [
      "What AI and agentic systems does Mobcoder AI build?",
      "How do you deploy production LLM agents?",
      "Share a relevant AI case study.",
    ],
    case_studies: [
      "Can you share Mobcoder AI case studies?",
      "What industries have you worked with?",
      "Tell me about a recent client project.",
    ],
    services: [
      "What services does Mobcoder AI offer?",
      "Do you offer staff augmentation?",
      "How does Mobcoder AI price projects?",
    ],
    pricing: [
      "How does Mobcoder AI approach pricing for custom software and AI projects?",
      "What's the difference between project-based and staff augmentation?",
      "I'd like a custom quote for my project.",
    ],
    contact: [
      "How can I contact Mobcoder AI to book a discovery call or discuss my project?",
      "What happens on a 30-minute discovery call?",
      "I'd like to schedule a discovery call.",
    ],
    about: [
      "Who is Mobcoder AI?",
      "What does Mobcoder AI specialize in?",
      "How does Mobcoder AI partner with clients?",
    ],
    general: [
      "What AI and agentic AI development services does Mobcoder AI offer?",
      "Can you share Mobcoder AI case studies and examples of AI projects you've delivered?",
      "How does Mobcoder AI approach pricing for custom software and AI projects?",
      "How can I contact Mobcoder AI to book a discovery call or discuss my project?",
    ],
  };

  function utmParams() {
    var out = {};
    try {
      var params = new URLSearchParams(new URL(_pageUrl()).search);
      ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"].forEach(function(k){
        var v = params.get(k);
        if (v) out[k] = v;
      });
    } catch (e) {}
    return out;
  }

  function track(event, cta) {
    var body = {
      session_id: getSessionId(),
      event: event,
      page_url: _pageUrl(),
      page_category: categoryFromPath(),
    };
    var utm = utmParams();
    if (Object.keys(utm).length) body.utm_params = utm;
    if (cta) body.cta = cta;
    fetch(EVENTS_URL, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body) }).catch(function(){});
  }

  var STAGE_ALIAS = { pitch:"qualify", close:"convert" };
  var STAGE_LABEL = { discover:"Discover", educate:"Educate", qualify:"Qualify", convert:"Convert" };
  var SCORE_COLOR = { cold:"#64748b", warm:"#f59e0b", hot:"#ef4444", qualified:"#10b981" };

  var ICON_CHAT = '<svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  var ICON_CLOSE = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var ICON_MINUS = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" viewBox="0 0 24 24"><line x1="5" y1="12" x2="19" y2="12"/></svg>';
  var ICON_SEND  = '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
  var ICON_BOT   = '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="6" r="3"/><path d="M12 9v2"/><circle cx="8" cy="16" r="1" fill="currentColor"/><circle cx="16" cy="16" r="1" fill="currentColor"/></svg>';
  var ICON_INFO  = '<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';
  var ICON_EXPAND   = '<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
  var ICON_COLLAPSE = '<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';

  var CSS = `
  #mc-root {
    /* ── Design tokens ─────────────────────────────────────────── */
    --mc-primary: ${PRIMARY};
    --mc-primary-strong: color-mix(in srgb, var(--mc-primary) 85%, #0f172a);
    --mc-primary-soft: color-mix(in srgb, var(--mc-primary) 9%, #ffffff);
    --mc-primary-softer: color-mix(in srgb, var(--mc-primary) 5%, #ffffff);
    --mc-primary-ring: color-mix(in srgb, var(--mc-primary) 32%, transparent);
    --mc-bg: #ffffff;
    --mc-surface: #f8fafc;
    --mc-surface-2: #f1f5f9;
    --mc-border: #e2e8f0;
    --mc-border-strong: #cbd5e1;
    --mc-text: #0f172a;
    --mc-text-2: #334155;
    --mc-text-muted: #64748b;
    --mc-text-faint: #94a3b8;
    --mc-user-bg: linear-gradient(135deg, var(--mc-primary), var(--mc-primary-strong));
    --mc-user-text: #ffffff;
    --mc-bot-bg: #FCF8F2;
    --mc-bot-border: #E9E2D8;
    --mc-bot-text: #0f172a;
    --mc-warm-muted: #98876B;
    --mc-success: #10b981;
    --mc-danger: #ef4444;
    --mc-shadow-lg: 0 40px 80px -16px rgba(15,23,42,.25), 0 12px 28px -8px rgba(15,23,42,.10), 0 0 0 1px rgba(255,255,255,.65) inset, 0 0 0 1px rgba(15,23,42,.05);
    --mc-shadow-md: 0 12px 32px -8px rgba(15,23,42,.16), 0 2px 8px rgba(15,23,42,.06);
    --mc-shadow-fab: 0 8px 24px color-mix(in srgb, var(--mc-primary) 38%, transparent), 0 2px 8px rgba(15,23,42,.12);
    --mc-radius: 22px;
    --mc-radius-md: 12px;
    --mc-radius-sm: 9px;
    --mc-font: "Inter", "SF Pro Text", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    --mc-ease: cubic-bezier(.32,.72,.33,1);
  }
  #mc-root * { box-sizing: border-box; margin: 0; padding: 0; }
  #mc-root button { -webkit-tap-highlight-color: transparent; }
  #mc-root :focus { outline: none; }
  #mc-root :focus-visible {
    outline: 2px solid var(--mc-primary);
    outline-offset: 2px;
    border-radius: 6px;
  }

  /* ── Floating action button ──────────────────────────────────── */
  #mc-fab {
    position: fixed; bottom: 28px; right: 28px; z-index: 2147483646;
    display: flex; align-items: center; gap: 10px;
    background: linear-gradient(135deg, var(--mc-primary) 0%, var(--mc-primary-strong) 100%);
    color: #fff;
    border: none; border-radius: 999px;
    padding: 15px 24px 15px 19px;
    cursor: pointer; font-family: var(--mc-font); font-weight: 600; font-size: 15px;
    letter-spacing: -0.01em;
    box-shadow: var(--mc-shadow-fab), inset 0 1px 0 rgba(255,255,255,.28);
    transition: transform .25s var(--mc-ease), box-shadow .25s var(--mc-ease);
    white-space: nowrap;
  }
  #mc-fab:hover {
    transform: translateY(-2px) scale(1.015);
    box-shadow: 0 14px 36px color-mix(in srgb, var(--mc-primary) 48%, transparent), 0 4px 12px rgba(15,23,42,.14);
  }
  #mc-fab:active { transform: translateY(0) scale(.99); }
  #mc-fab::before {
    content: "";
    position: absolute; inset: -6px; border-radius: 999px;
    border: 2px solid var(--mc-primary); opacity: 0;
    animation: mc-pulse 3s var(--mc-ease) infinite;
    pointer-events: none;
  }
  @keyframes mc-pulse { 0% { transform:scale(.92);opacity:.5; } 70% { transform:scale(1.12);opacity:0; } 100% { opacity:0; } }
  #mc-fab-badge {
    position: absolute; top: -5px; right: -5px;
    background: var(--mc-danger); color: #fff; border-radius: 999px;
    width: 19px; height: 19px; font-size: 10px; font-weight: 700;
    font-family: var(--mc-font); display: none; align-items: center;
    justify-content: center; border: 2px solid #fff;
    box-shadow: 0 2px 6px rgba(239,68,68,.4);
  }
  #mc-fab-badge.show { display: flex; animation: mc-pop .3s var(--mc-ease); }
  @keyframes mc-pop { 0% { transform:scale(0); } 80% { transform:scale(1.15); } 100% { transform:scale(1); } }

  /* ── Panel ───────────────────────────────────────────────────── */
  #mc-panel {
    position: fixed; bottom: 100px; right: 28px; z-index: 2147483645;
    width: 408px; height: min(680px, calc(100vh - 130px));
    display: flex; flex-direction: column;
    background: rgba(255,255,255,.88);
    backdrop-filter: blur(24px) saturate(1.6);
    -webkit-backdrop-filter: blur(24px) saturate(1.6);
    border-radius: var(--mc-radius);
    box-shadow: var(--mc-shadow-lg); border: 1px solid rgba(255,255,255,.7);
    font-family: var(--mc-font); overflow: hidden;
    opacity: 0; transform: translateY(16px) scale(.97); pointer-events: none;
    transform-origin: bottom right;
    transition: opacity .3s var(--mc-ease), transform .3s var(--mc-ease),
                width .3s var(--mc-ease), height .3s var(--mc-ease);
  }
  #mc-panel.open { opacity:1; transform:translateY(0) scale(1); pointer-events:auto; }
  #mc-panel.expanded {
    width: min(760px, calc(100vw - 56px));
    height: min(85vh, calc(100vh - 130px));
  }
  @media(max-width:520px) {
    #mc-panel, #mc-panel.expanded {
      width:calc(100vw - 16px); right:8px; bottom:82px;
      height:min(76vh, calc(100vh - 100px));
    }
    #mc-fab { bottom:16px; right:16px; padding:13px 19px 13px 15px; font-size:14px; }
  }

  /* ── Header ──────────────────────────────────────────────────── */
  #mc-header {
    background:
      radial-gradient(120% 180% at 100% 0%, color-mix(in srgb, var(--mc-primary) 55%, transparent) 0%, transparent 55%),
      linear-gradient(135deg, #0b1220 0%, #101c33 55%, var(--mc-primary-strong) 130%);
    padding: 17px 16px 15px; display: flex; align-items: center; gap: 12px;
    flex-shrink: 0; position: relative;
    border-bottom: 1px solid rgba(255,255,255,.06);
  }
  #mc-header::after {
    content:""; position:absolute; left:0; right:0; bottom:-1px; height:1px;
    background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--mc-primary) 45%, transparent), transparent);
  }
  #mc-header-avatar {
    width: 40px; height: 40px; border-radius: 13px;
    background: linear-gradient(135deg, color-mix(in srgb, var(--mc-primary) 55%, #fff) 0%, var(--mc-primary) 60%, var(--mc-primary-strong) 100%);
    display: flex; align-items: center;
    justify-content: center; font-size: 18px; flex-shrink: 0; overflow: hidden;
    border: 1px solid rgba(255,255,255,.35);
    color: #fff;
    box-shadow: inset 0 1px 0 rgba(255,255,255,.35), 0 4px 14px color-mix(in srgb, var(--mc-primary) 55%, transparent);
  }
  #mc-header-avatar img { width:100%; height:100%; object-fit:cover; }
  #mc-header-info { flex:1; min-width:0; }
  #mc-header-name {
    color:#fff; font-weight:600; font-size:14.5px; letter-spacing:-0.01em;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  #mc-header-sub { color:rgba(255,255,255,.62); font-size:12px; margin-top:3px; display:flex; align-items:center; gap:6px; }
  #mc-status-dot {
    width:7px; height:7px; border-radius:50%; background:var(--mc-success); flex-shrink:0;
    box-shadow:0 0 0 3px rgba(16,185,129,.22); animation:mc-blink 2.4s ease-in-out infinite;
  }
  @keyframes mc-blink { 0%,100%{opacity:1;} 50%{opacity:.45;} }
  #mc-header-actions { display:flex; gap:6px; }
  .mc-head-btn {
    width:30px; height:30px; border-radius:9px;
    background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.14);
    color:rgba(255,255,255,.82); cursor:pointer;
    display:flex; align-items:center; justify-content:center;
    transition:background .15s, transform .15s;
  }
  .mc-head-btn:hover { background:rgba(255,255,255,.18); }
  .mc-head-btn:active { transform:scale(.94); }
  #mc-toast {
    position:absolute; top:12px; left:50%; transform:translateX(-50%) translateY(-4px);
    background:rgba(15,23,42,.92); color:#fff; border-radius:999px;
    backdrop-filter: blur(8px);
    padding:6px 16px; font-size:12px; font-weight:500; white-space:nowrap;
    box-shadow: var(--mc-shadow-md);
    opacity:0; pointer-events:none; transition:opacity .2s var(--mc-ease), transform .2s var(--mc-ease); z-index:10;
  }
  #mc-toast.show { opacity:1; transform:translateX(-50%) translateY(0); }

  /* ── Context bar ─────────────────────────────────────────────── */
  #mc-context-bar {
    background:var(--mc-primary-softer); border-bottom:1px solid var(--mc-border);
    padding:7px 14px; font-size:11.5px; color:var(--mc-text-muted);
    display:none; align-items:center; gap:6px; flex-shrink:0;
  }
  #mc-context-bar strong { color:var(--mc-text-2); font-weight:600; }
  #mc-context-bar.show { display:flex; }

  /* ── Messages ────────────────────────────────────────────────── */
  #mc-messages {
    flex:1; overflow-y:auto; padding:18px 16px 8px;
    display:flex; flex-direction:column; gap:14px;
    scroll-behavior:smooth;
    background:
      radial-gradient(90% 46% at 50% 0%, var(--mc-primary-softer) 0%, transparent 72%),
      linear-gradient(180deg, rgba(250,246,239,.75), rgba(255,255,255,.5));
    overscroll-behavior: contain;
  }
  #mc-messages::-webkit-scrollbar { width:5px; }
  #mc-messages::-webkit-scrollbar-thumb { background:var(--mc-border-strong); border-radius:999px; }
  #mc-messages::-webkit-scrollbar-thumb:hover { background:var(--mc-text-faint); }
  .mc-row { display:flex; align-items:flex-end; gap:8px; animation: mc-msg-in .32s var(--mc-ease) both; }
  @keyframes mc-msg-in { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
  .mc-row.user { flex-direction:row-reverse; }
  .mc-avatar-sm {
    width:28px; height:28px; border-radius:9px;
    background:var(--mc-surface); border:1px solid var(--mc-border);
    color: var(--mc-text-muted);
    display:flex; align-items:center; justify-content:center;
    font-size:13px; flex-shrink:0; overflow:hidden;
  }
  .mc-avatar-sm img { width:100%; height:100%; object-fit:cover; }
  .mc-bubble {
    max-width:80%; padding:11px 15px; border-radius:16px;
    font-size:14px; line-height:1.65; word-break:break-word;
    letter-spacing:-0.005em;
  }
  .mc-row.user .mc-bubble {
    background:var(--mc-user-bg); color:var(--mc-user-text);
    border-bottom-right-radius:5px;
    box-shadow: 0 2px 8px color-mix(in srgb, var(--mc-primary) 24%, transparent);
  }
  .mc-row.bot .mc-bubble {
    background:var(--mc-bot-bg); color:var(--mc-bot-text);
    border:1px solid var(--mc-bot-border);
    border-radius:20px;
    box-shadow: 0 8px 30px rgba(0,0,0,.06);
    padding:16px 18px 15px 20px;
    line-height:1.7; position:relative;
    transition: transform .2s var(--mc-ease), box-shadow .2s var(--mc-ease);
  }
  .mc-row.bot .mc-bubble::before {
    content:""; position:absolute; left:-1px; top:16px; bottom:16px; width:4px;
    border-radius:0 4px 4px 0;
    background:linear-gradient(180deg, var(--mc-primary), var(--mc-primary-strong));
  }
  .mc-row.bot .mc-bubble:hover {
    transform:translateY(-2px);
    box-shadow: 0 14px 40px rgba(0,0,0,.09);
  }
  .mc-ai-head {
    display:flex; align-items:center; justify-content:space-between; gap:8px;
    margin-bottom:10px; padding-bottom:9px;
    border-bottom:1px solid rgba(233,226,216,.9);
  }
  .mc-ai-title {
    font-size:10.5px; font-weight:700; letter-spacing:.07em; text-transform:uppercase;
    color:var(--mc-warm-muted);
  }
  .mc-ai-badge {
    font-size:9px; font-weight:650; letter-spacing:.05em; text-transform:uppercase;
    color:var(--mc-primary-strong);
    background:color-mix(in srgb, var(--mc-primary) 8%, #fff);
    border:1px solid color-mix(in srgb, var(--mc-primary) 20%, #fff);
    border-radius:999px; padding:2.5px 8px; white-space:nowrap;
  }
  .mc-content { min-width:0; }
  .mc-row.bot .mc-content, .mc-row.bot .mc-content .mc-p, .mc-row.bot .mc-content li { font-size:15px; line-height:1.7; }
  .mc-bubble.mc-streaming::after, .mc-content.mc-streaming::after {
    content:""; display:inline-block; width:7px; height:14px; margin-left:3px;
    vertical-align:text-bottom; border-radius:2px;
    background: var(--mc-primary); opacity:.7;
    animation: mc-caret 1s steps(2, start) infinite;
  }
  @keyframes mc-caret { 50% { opacity:0; } }
  .mc-bubble a { color:var(--mc-primary); text-decoration:underline; text-underline-offset:2px; font-weight:500; }
  .mc-bubble a:hover { text-decoration:none; }
  .mc-row.user .mc-bubble a { color:#fff; }
  .mc-bubble strong { font-weight:650; color:var(--mc-text); }
  .mc-row.user .mc-bubble strong { color:#fff; }
  .mc-bubble em { font-style:italic; color:var(--mc-text-2); }
  .mc-bubble code {
    background:var(--mc-primary-soft); color:var(--mc-primary-strong);
    padding:2px 6px; border-radius:5px;
    font-family:"SF Mono","Fira Mono",Consolas,monospace; font-size:12.5px;
    border:1px solid color-mix(in srgb, var(--mc-primary) 16%, transparent);
  }
  .mc-bubble hr { border:none; border-top:1px solid var(--mc-border); margin:10px 0; }
  .mc-bubble ul { padding-left:18px; margin-top:4px; }
  .mc-bubble li { margin-bottom:3px; }
  .mc-p { margin:0 0 9px; font-size:14px; line-height:1.7; }
  .mc-p:last-child { margin-bottom:0; }
  .mc-h { font-weight:650; margin:12px 0 5px; color:var(--mc-text); line-height:1.35; letter-spacing:-0.01em; }
  h3.mc-h { font-size:14.5px; }
  h4.mc-h { font-size:14px; }
  h5.mc-h { font-size:13.5px; }
  .mc-ul, .mc-ol { padding-left:20px; margin:5px 0 9px; }
  .mc-ul li, .mc-ol li { margin-bottom:5px; line-height:1.65; font-size:14px; }
  .mc-ul li::marker, .mc-ol li::marker { color:var(--mc-primary); font-weight:600; }
  .mc-bq {
    border-left:3px solid var(--mc-primary); padding:6px 12px; color:var(--mc-text-muted);
    margin:7px 0; background:var(--mc-surface); border-radius:0 8px 8px 0;
    font-style:italic; font-size:13.5px; line-height:1.6;
  }
  .mc-gap { height:8px; display:block; }
  .mc-ts { font-size:10.5px; color:var(--mc-text-faint); margin-top:4px; font-variant-numeric:tabular-nums; }
  .mc-row.user .mc-ts { text-align:right; }

  /* ── Citations & meta ────────────────────────────────────────── */
  .mc-citations { margin-top:12px; padding-top:10px; border-top:1px solid rgba(233,226,216,.9); display:flex; flex-direction:column; gap:6px; }
  .mc-citation {
    display:flex; align-items:center; gap:8px; font-size:12px; color:var(--mc-text-2);
    background:#fff; border:1px solid var(--mc-bot-border); border-radius:10px;
    padding:8px 12px; text-decoration:none; font-weight:550;
    box-shadow: 0 1px 3px rgba(0,0,0,.04);
    transition:border-color .15s, background .15s, transform .15s, box-shadow .15s;
  }
  .mc-citation:hover { background:var(--mc-primary-softer); border-color:color-mix(in srgb, var(--mc-primary) 35%, var(--mc-border)); transform:translateX(2px); box-shadow:0 3px 10px rgba(0,0,0,.07); }
  .mc-citation-dot { width:6px; height:6px; border-radius:50%; background:var(--mc-primary); flex-shrink:0; }
  .mc-cite-cards { margin-top:12px; padding-top:10px; border-top:1px solid rgba(233,226,216,.9); display:flex; flex-direction:column; gap:8px; }
  .mc-cite-card {
    display:block; background:#fff; border:1px solid var(--mc-bot-border); border-radius:12px;
    padding:10px 12px; text-decoration:none; color:inherit;
    box-shadow:0 1px 4px rgba(0,0,0,.05);
    transition:border-color .15s, box-shadow .15s, transform .15s;
  }
  .mc-cite-card:hover { border-color:color-mix(in srgb, var(--mc-primary) 35%, var(--mc-border)); box-shadow:0 4px 12px rgba(37,99,235,.12); transform:translateY(-1px); }
  .mc-cite-card-title { font-size:12.5px; font-weight:650; color:var(--mc-text); margin-bottom:4px; line-height:1.35; }
  .mc-cite-card-snippet { font-size:11.5px; color:var(--mc-text-muted); line-height:1.45; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; margin-bottom:6px; }
  .mc-cite-card-link { font-size:11px; font-weight:650; color:var(--mc-primary); }
  .mc-meta-row { display:flex; gap:6px; margin-top:8px; flex-wrap:wrap; }
  .mc-pill { font-size:10.5px; padding:3px 9px; border-radius:999px; border:1px solid var(--mc-border); color:var(--mc-text-2); background:#fff; font-weight:600; letter-spacing:.02em; }
  .mc-pill.score { color:#fff; border:none; }

  /* ── Typing indicator ────────────────────────────────────────── */
  #mc-typing {
    display:none; align-items:center; gap:5px; padding:12px 16px;
    background:var(--mc-bot-bg); border:1px solid var(--mc-bot-border);
    box-shadow: 0 4px 16px rgba(0,0,0,.05);
    border-radius:16px; border-bottom-left-radius:5px; width:fit-content;
  }
  #mc-typing span { width:6px; height:6px; border-radius:50%; background:var(--mc-text-faint); animation:mc-typing 1.2s ease-in-out infinite; }
  #mc-typing span:nth-child(2){animation-delay:.2s;} #mc-typing span:nth-child(3){animation-delay:.4s;}
  @keyframes mc-typing { 0%,80%,100%{transform:scale(1);opacity:.5;} 40%{transform:scale(1.3);opacity:1;} }

  /* ── Booking banner ──────────────────────────────────────────── */
  #mc-book-banner {
    display:none; margin:0 16px 10px;
    background:linear-gradient(135deg,#0b1220, var(--mc-primary-strong) 130%);
    border-radius:var(--mc-radius-md); padding:13px 38px 13px 15px; align-items:center; gap:10px;
    position:relative; box-shadow: var(--mc-shadow-md);
    animation: mc-msg-in .32s var(--mc-ease) both;
  }
  #mc-book-banner.show { display:flex; }
  #mc-book-banner-text { flex:1; min-width:0; }
  #mc-book-banner-text strong { color:#fff; font-size:13px; font-weight:650; display:block; letter-spacing:-0.01em; }
  #mc-book-banner-text span { color:rgba(255,255,255,.72); font-size:11.5px; }
  #mc-book-btn {
    background:#fff; color:var(--mc-primary-strong); border:none; border-radius:var(--mc-radius-sm);
    padding:8px 15px; font-size:12px; font-weight:700; font-family:var(--mc-font);
    cursor:pointer; white-space:nowrap; flex-shrink:0; text-decoration:none; display:inline-block;
    transition: transform .15s, box-shadow .15s;
  }
  #mc-book-btn:hover { transform:translateY(-1px); box-shadow:0 4px 12px rgba(0,0,0,.25); }
  .mc-dismiss {
    position:absolute; top:8px; right:8px; width:24px; height:24px;
    border:none; border-radius:50%; background:rgba(255,255,255,.16); color:#fff;
    cursor:pointer; font-size:16px; line-height:1; display:flex; align-items:center;
    justify-content:center; font-family:var(--mc-font); padding:0;
    transition:background .15s;
  }
  .mc-dismiss:hover { background:rgba(255,255,255,.32); }

  /* ── Suggestions & quick chips ───────────────────────────────── */
  #mc-suggest-wrap { display:none; position:relative; padding:0 14px 8px; flex-shrink:0; }
  #mc-suggest-wrap.show { display:block; }
  #mc-suggest-wrap .mc-dismiss { top:0; right:6px; color:var(--mc-text-muted); background:var(--mc-surface-2); }
  #mc-suggest-wrap .mc-dismiss:hover { background:var(--mc-border); }
  #mc-quick { padding:9px 14px 5px; display:flex; flex-wrap:wrap; gap:6px; flex-shrink:0; border-top:1px solid rgba(226,232,240,.7); background:rgba(255,255,255,.7); }
  .mc-qchip {
    font-size:12px; padding:6px 13px; border-radius:999px;
    border:1px solid rgba(226,232,240,.9); background:#fff;
    box-shadow: 0 1px 2px rgba(15,23,42,.04);
    color:var(--mc-text-2); cursor:pointer; font-family:var(--mc-font); font-weight:550;
    transition:background .15s,border-color .15s,color .15s,transform .15s; white-space:nowrap;
  }
  .mc-qchip:hover { background:var(--mc-primary-soft); border-color:color-mix(in srgb, var(--mc-primary) 40%, var(--mc-border)); color:var(--mc-primary); transform:translateY(-1px); }
  .mc-qchip:active { transform:translateY(0); }
  #mc-suggest { padding:24px 0 0; display:flex; flex-wrap:wrap; gap:6px; min-height:0; }
  .mc-schip {
    font-size:12px; padding:6px 13px; border-radius:999px;
    border:1px solid color-mix(in srgb, var(--mc-primary) 22%, rgba(255,255,255,.6));
    background:rgba(255,255,255,.55);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    color:var(--mc-primary-strong);
    cursor:pointer; font-family:var(--mc-font); font-weight:550;
    box-shadow: 0 1px 3px rgba(15,23,42,.05);
    transition:background .15s, transform .15s, box-shadow .15s; white-space:nowrap;
  }
  .mc-schip:hover { background:color-mix(in srgb, var(--mc-primary) 10%, #fff); transform:translateY(-2px); box-shadow: 0 4px 12px rgba(37,99,235,.14); }
  .mc-schip:active { transform:translateY(0); }

  /* ── Lead qualify form ───────────────────────────────────────── */
  #mc-qualify {
    display:none; padding:14px 16px; border-top:1px solid var(--mc-border);
    flex-direction:column; gap:9px; flex-shrink:0; background:var(--mc-surface);
  }
  #mc-qualify.show { display:flex; }
  #mc-qualify-title { font-size:11.5px; font-weight:650; color:var(--mc-text-muted); text-transform:uppercase; letter-spacing:.06em; }
  .mc-field { display:flex; flex-direction:column; gap:3px; }
  .mc-field input {
    border:1px solid var(--mc-border); border-radius:var(--mc-radius-sm); padding:9px 12px;
    font-size:13px; font-family:var(--mc-font); outline:none; background:#fff;
    color:var(--mc-text);
    transition:border-color .15s, box-shadow .15s;
  }
  .mc-field input:focus { border-color:var(--mc-primary); box-shadow:0 0 0 3px var(--mc-primary-ring); }
  .mc-field input::placeholder { color:var(--mc-text-faint); }
  #mc-qualify-submit {
    background:linear-gradient(135deg, var(--mc-primary), var(--mc-primary-strong)); color:#fff; border:none; border-radius:var(--mc-radius-sm);
    padding:10px; font-size:13px; font-weight:650; font-family:var(--mc-font);
    cursor:pointer; transition:filter .15s, transform .15s;
    box-shadow: 0 2px 8px color-mix(in srgb, var(--mc-primary) 30%, transparent);
  }
  #mc-qualify-submit:hover { filter:brightness(1.06); transform:translateY(-1px); }
  #mc-qualify-submit:active { transform:translateY(0); }

  /* ── Input row ───────────────────────────────────────────────── */
  #mc-input-row {
    display:flex; align-items:flex-end; gap:0;
    border-top:1px solid rgba(226,232,240,.7); padding:12px 13px;
    flex-shrink:0; background:rgba(255,255,255,.7);
  }
  #mc-input {
    flex:1; border:1px solid rgba(226,232,240,.9); border-radius:15px;
    padding:11px 16px; font-size:14px; font-family:var(--mc-font);
    color:var(--mc-text); background:#fff;
    box-shadow: 0 1px 3px rgba(15,23,42,.05);
    outline:none; resize:none; max-height:96px; line-height:1.45;
    transition:border-color .15s, box-shadow .15s;
  }
  #mc-input:focus { border-color:var(--mc-primary); box-shadow:0 0 0 3px var(--mc-primary-ring), 0 1px 3px rgba(15,23,42,.05); }
  #mc-input::placeholder { color:var(--mc-text-faint); }
  #mc-send {
    margin-left:9px; width:40px; height:40px; flex-shrink:0;
    background:linear-gradient(135deg, var(--mc-primary), var(--mc-primary-strong)); border:none; border-radius:13px;
    color:#fff; cursor:pointer; display:flex; align-items:center; justify-content:center;
    box-shadow: 0 2px 8px color-mix(in srgb, var(--mc-primary) 32%, transparent);
    transition:filter .15s, transform .15s, opacity .15s;
  }
  #mc-send:hover { filter:brightness(1.08); transform:translateY(-1px); }
  #mc-send:active { transform:scale(.95); }
  #mc-send:disabled { opacity:.4; cursor:not-allowed; transform:none; filter:none; }
  #mc-power {
    display:flex; justify-content:center; padding:6px 0 9px;
    font-size:10.5px; color:var(--mc-text-faint); font-family:var(--mc-font);
    background:var(--mc-bg); letter-spacing:.01em;
  }
  #mc-power a { color:var(--mc-text-faint); text-decoration:none; font-weight:550; }
  #mc-power a:hover { color:var(--mc-text-muted); }

  /* ── Feedback ────────────────────────────────────────────────── */
  .mc-feedback-row { display:flex; align-items:center; gap:4px; margin-top:6px; }
  .mc-feedback-row span { font-size:10.5px; color:var(--mc-text-faint); margin-right:2px; }
  .mc-fb-btn {
    background:none; border:1px solid var(--mc-border); border-radius:7px;
    padding:3px 8px; font-size:13px; cursor:pointer; line-height:1;
    transition:background .15s,border-color .15s,transform .15s;
  }
  .mc-fb-btn:hover { background:var(--mc-surface-2); border-color:var(--mc-border-strong); transform:translateY(-1px); }
  .mc-fb-btn.active-pos { background:#dcfce7; border-color:#86efac; }
  .mc-fb-btn.active-neg { background:#fee2e2; border-color:#fca5a5; }
  .mc-fb-btn:disabled { opacity:.45; cursor:default; transform:none; }
  .mc-fb-comment-box textarea:focus { border-color:var(--mc-primary) !important; box-shadow:0 0 0 3px var(--mc-primary-ring); }

  /* ── Exit intent ─────────────────────────────────────────────── */
  #mc-exit-overlay {
    display:none; position:fixed; inset:0; z-index:2147483647;
    background:rgba(11,18,32,.55); backdrop-filter:blur(4px);
    align-items:center; justify-content:center;
  }
  #mc-exit-overlay.show { display:flex; }
  #mc-exit-card {
    background:#fff; border-radius:20px; box-shadow:var(--mc-shadow-lg);
    padding:30px 30px 24px; max-width:380px; width:calc(100vw - 40px);
    font-family:var(--mc-font); position:relative;
    animation: mc-msg-in .3s var(--mc-ease) both;
  }
  #mc-exit-card h3 { font-size:19px; font-weight:750; color:var(--mc-text); margin-bottom:6px; letter-spacing:-0.02em; }
  #mc-exit-card p { font-size:13.5px; color:var(--mc-text-muted); line-height:1.55; margin-bottom:16px; }
  #mc-exit-input {
    width:100%; border:1px solid var(--mc-border); border-radius:var(--mc-radius-sm);
    padding:10px 13px; font-size:13.5px; font-family:var(--mc-font); outline:none;
    margin-bottom:10px; box-sizing:border-box; background:var(--mc-surface);
    transition:border-color .15s, box-shadow .15s, background .15s;
  }
  #mc-exit-input:focus { border-color:var(--mc-primary); background:#fff; box-shadow:0 0 0 3px var(--mc-primary-ring); }
  #mc-exit-actions { display:flex; gap:8px; }
  #mc-exit-chat {
    flex:1; background:linear-gradient(135deg, var(--mc-primary), var(--mc-primary-strong)); color:#fff; border:none;
    border-radius:var(--mc-radius-sm); padding:11px; font-size:13.5px; font-weight:700;
    font-family:var(--mc-font); cursor:pointer; transition:filter .15s, transform .15s;
  }
  #mc-exit-chat:hover { filter:brightness(1.06); transform:translateY(-1px); }
  #mc-exit-book {
    flex:1; background:#0b1220; color:#fff; border:none; border-radius:var(--mc-radius-sm);
    padding:11px; font-size:13.5px; font-weight:700; font-family:var(--mc-font);
    cursor:pointer; text-decoration:none; display:flex; align-items:center; justify-content:center;
    transition:filter .15s, transform .15s;
  }
  #mc-exit-book:hover { filter:brightness(1.35); transform:translateY(-1px); }
  #mc-exit-dismiss { position:absolute; top:12px; right:14px; background:none; border:none; font-size:20px; color:var(--mc-text-faint); cursor:pointer; line-height:1; }
  #mc-exit-dismiss:hover { color:var(--mc-text-2); }

  /* ── Privacy + escalation ────────────────────────────────────── */
  #mc-privacy-copy { font-size:11.5px; line-height:1.45; color:var(--mc-text-muted); }
  #mc-escalate {
    display:none; padding:14px 16px; border-top:1px solid var(--mc-border);
    flex-direction:column; gap:9px; flex-shrink:0; background:#fff7ed;
  }
  #mc-escalate.show { display:flex; }
  #mc-escalate input {
    border:1px solid var(--mc-border); border-radius:var(--mc-radius-sm); padding:9px 12px;
    font-size:13px; font-family:var(--mc-font); outline:none; background:#fff;
    transition:border-color .15s, box-shadow .15s;
  }
  #mc-escalate input:focus { border-color:#ea580c; box-shadow:0 0 0 3px rgba(234,88,12,.18); }
  #mc-escalate-title { font-size:13px; font-weight:700; color:#9a3412; }
  #mc-escalate-copy { font-size:12px; color:#7c2d12; line-height:1.45; }
  #mc-escalate-submit {
    background:#ea580c; color:#fff; border:none; border-radius:var(--mc-radius-sm);
    padding:9px; font-size:13px; font-weight:650; font-family:var(--mc-font); cursor:pointer;
    transition:filter .15s;
  }
  #mc-escalate-submit:hover { filter:brightness(1.08); }
  #mc-escalate-cancel { background:transparent; color:var(--mc-text-muted); border:none; font-size:12px; cursor:pointer; font-family:var(--mc-font); }

  /* ── Reduced motion ──────────────────────────────────────────── */
  @media (prefers-reduced-motion: reduce) {
    #mc-root *, #mc-root *::before, #mc-root *::after {
      animation-duration: .01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: .01ms !important;
    }
    #mc-messages { scroll-behavior: auto; }
  }
`;

  var isOpen = false;
  var isSending = false;
  var unread = 0;
  var qInputs = {};

  function build() {
    var styleEl = document.createElement("style");
    styleEl.textContent = CSS;
    document.head.appendChild(styleEl);

    var root = document.createElement("div");
    root.id = "mc-root";

    /* FAB */
    var fab = document.createElement("button");
    fab.id = "mc-fab";
    fab.setAttribute("aria-label", "Open chat with " + COMPANY);
    fab.setAttribute("aria-haspopup", "dialog");
    fab.setAttribute("aria-controls", "mc-panel");
    fab.setAttribute("aria-expanded", "false");
    fab.innerHTML = ICON_CHAT + " Chat with " + COMPANY;
    var badge = document.createElement("div");
    badge.id = "mc-fab-badge";
    badge.textContent = "1";
    fab.appendChild(badge);

    /* PANEL */
    var panel = document.createElement("div");
    panel.id = "mc-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", AGENT_NAME + " chat");

    /* Toast */
    var toast = document.createElement("div");
    toast.id = "mc-toast";

    /* Header */
    var avatarEl = document.createElement("div");
    avatarEl.id = "mc-header-avatar";
    if (AVATAR_URL) {
      var img = document.createElement("img");
      img.src = AVATAR_URL; img.alt = AGENT_NAME;
      avatarEl.appendChild(img);
    } else {
      avatarEl.innerHTML = ICON_BOT;
    }

    var headerInfo = document.createElement("div");
    headerInfo.id = "mc-header-info";
    var headerName = document.createElement("div");
    headerName.id = "mc-header-name";
    headerName.textContent = AGENT_NAME.toLowerCase().indexOf(COMPANY.toLowerCase()) !== -1
      ? AGENT_NAME
      : AGENT_NAME + " · " + COMPANY;
    var subRow = document.createElement("div");
    subRow.id = "mc-header-sub";
    var dot = document.createElement("div");
    dot.id = "mc-status-dot";
    subRow.appendChild(dot);
    subRow.appendChild(document.createTextNode("Online · Replies instantly"));
    headerInfo.appendChild(headerName);
    headerInfo.appendChild(subRow);

    var headerActions = document.createElement("div");
    headerActions.id = "mc-header-actions";
    var expandBtn = document.createElement("button");
    expandBtn.className = "mc-head-btn";
    expandBtn.title = "Expand";
    expandBtn.setAttribute("aria-label", "Expand chat window");
    expandBtn.innerHTML = ICON_EXPAND;
    var minBtn = document.createElement("button");
    minBtn.className = "mc-head-btn";
    minBtn.title = "Minimise";
    minBtn.setAttribute("aria-label", "Minimise chat");
    minBtn.innerHTML = ICON_MINUS;
    var closeBtn = document.createElement("button");
    closeBtn.className = "mc-head-btn";
    closeBtn.title = "Close";
    closeBtn.setAttribute("aria-label", "Close chat");
    closeBtn.innerHTML = ICON_CLOSE;
    headerActions.appendChild(expandBtn);
    headerActions.appendChild(minBtn);
    headerActions.appendChild(closeBtn);

    var header = document.createElement("div");
    header.id = "mc-header";
    header.appendChild(avatarEl);
    header.appendChild(headerInfo);
    header.appendChild(headerActions);
    header.appendChild(toast);

    /* Context bar */
    var ctxBar = document.createElement("div");
    ctxBar.id = "mc-context-bar";
    ctxBar.innerHTML = ICON_INFO + '&nbsp;You\'re chatting from <strong id="mc-ctx-page"></strong>';

    /* Messages */
    var messages = document.createElement("div");
    messages.id = "mc-messages";
    messages.setAttribute("aria-live", "polite");

    /* Typing */
    var typingRow = document.createElement("div");
    typingRow.className = "mc-row bot";
    var typingEl = document.createElement("div");
    typingEl.id = "mc-typing";
    typingEl.appendChild(document.createElement("span"));
    typingEl.appendChild(document.createElement("span"));
    typingEl.appendChild(document.createElement("span"));
    typingRow.appendChild(typingEl);

    /* Book banner */
    var bookBanner = document.createElement("div");
    bookBanner.id = "mc-book-banner";
    var bookText = document.createElement("div");
    bookText.id = "mc-book-banner-text";
    bookText.innerHTML = "<strong>Ready to talk to our team?</strong><span>Book a free 30-min discovery call</span>";
    var bookBtn = document.createElement("a");
    bookBtn.id = "mc-book-btn";
    bookBtn.href = BOOKING_URL;
    bookBtn.target = "_blank";
    bookBtn.rel = "noopener noreferrer";
    bookBtn.textContent = "Book Now";
    bookBtn.onclick = function(){ track("booking_cta_clicked","book_banner"); };
    bookBanner.appendChild(bookText);
    bookBanner.appendChild(bookBtn);
    var bookClose = document.createElement("button");
    bookClose.type = "button";
    bookClose.className = "mc-dismiss";
    bookClose.setAttribute("aria-label", "Dismiss");
    bookClose.textContent = "\u00d7";
    bookBanner.appendChild(bookClose);

    /* Suggest */
    var suggestWrap = document.createElement("div");
    suggestWrap.id = "mc-suggest-wrap";
    var suggestClose = document.createElement("button");
    suggestClose.type = "button";
    suggestClose.className = "mc-dismiss";
    suggestClose.setAttribute("aria-label", "Dismiss suggestions");
    suggestClose.textContent = "\u00d7";
    var suggest = document.createElement("div");
    suggest.id = "mc-suggest";
    suggestWrap.appendChild(suggestClose);
    suggestWrap.appendChild(suggest);

    /* Qualify form */
    var qualify = document.createElement("div");
    qualify.id = "mc-qualify";
    var qTitle = document.createElement("div");
    qTitle.id = "mc-qualify-title";
    qTitle.textContent = "Share a bit about your project";
    qualify.appendChild(qTitle);
    var privacy = document.createElement("div");
    privacy.id = "mc-privacy-copy";
    privacy.textContent = PRIVACY_COPY;
    qualify.appendChild(privacy);
    var qFieldDefs = [
      { key:"name",         placeholder:"Your name" },
      { key:"email",        placeholder:"Work email" },
      { key:"company",      placeholder:"Company" },
      { key:"project_need", placeholder:"What are you building?" },
    ];
    qInputs = {};
    qFieldDefs.forEach(function(f){
      var field = document.createElement("div");
      field.className = "mc-field";
      var inp = document.createElement("input");
      inp.type = f.key === "email" ? "email" : "text";
      inp.placeholder = f.placeholder;
      qInputs[f.key] = inp;
      field.appendChild(inp);
      qualify.appendChild(field);
    });
    var qSubmit = document.createElement("button");
    qSubmit.id = "mc-qualify-submit";
    qSubmit.textContent = "Send →";
    qualify.appendChild(qSubmit);

    /* Human escalation form */
    var escalate = document.createElement("div");
    escalate.id = "mc-escalate";
    var escTitle = document.createElement("div");
    escTitle.id = "mc-escalate-title";
    escTitle.textContent = "Talk to our team";
    var escCopy = document.createElement("div");
    escCopy.id = "mc-escalate-copy";
    escCopy.textContent = "Share your email and a brief note — we'll reach out within 1 business day.";
    var escName = document.createElement("input");
    escName.type = "text";
    escName.placeholder = "Your name";
    var escEmail = document.createElement("input");
    escEmail.type = "email";
    escEmail.placeholder = "Work email";
    var escMsg = document.createElement("textarea");
    escMsg.placeholder = "How can we help?";
    escMsg.rows = 3;
    escMsg.style.cssText = "border:1px solid var(--mc-border);border-radius:8px;padding:7px 10px;font-size:13px;font-family:var(--mc-font);resize:none;";
    var escSubmit = document.createElement("button");
    escSubmit.id = "mc-escalate-submit";
    escSubmit.textContent = "Send to our team";
    var escCancel = document.createElement("button");
    escCancel.id = "mc-escalate-cancel";
    escCancel.textContent = "Cancel";
    escalate.appendChild(escTitle);
    escalate.appendChild(escCopy);
    escalate.appendChild(escName);
    escalate.appendChild(escEmail);
    escalate.appendChild(escMsg);
    escalate.appendChild(escSubmit);
    escalate.appendChild(escCancel);

    /* Quick chips */
    var quick = document.createElement("div");
    quick.id = "mc-quick";
    QUICK_CHIPS.forEach(function(pair){
      var c = document.createElement("button");
      c.className = "mc-qchip";
      c.textContent = pair[0];
      c.onclick = (function(msg, href, label){
        return function(){
          track("cta_clicked", "quick_chip");
          if (href) {
            track("booking_cta_clicked", label || "quick_chip");
            window.open(href, "_blank", "noopener,noreferrer");
            return;
          }
          doSend(msg);
        };
      })(pair[1], pair[2], pair[0]);
      quick.appendChild(c);
    });

    /* Input row */
    var inputRow = document.createElement("div");
    inputRow.id = "mc-input-row";
    var input = document.createElement("textarea");
    input.id = "mc-input";
    input.placeholder = "Ask " + AGENT_NAME + " anything…";
    input.rows = 1;
    input.setAttribute("aria-label", "Message input");
    var sendBtn = document.createElement("button");
    sendBtn.id = "mc-send";
    sendBtn.setAttribute("aria-label", "Send message");
    sendBtn.innerHTML = ICON_SEND;
    inputRow.appendChild(input);
    inputRow.appendChild(sendBtn);

    /* Power */
    var power = document.createElement("div");
    power.id = "mc-power";
    var powerLabel = COMPANY.toLowerCase().indexOf("ai") !== -1 ? COMPANY : COMPANY + " AI";
    power.innerHTML = 'Powered by <a href="https://mobcoder.ai" target="_blank" rel="noopener">&nbsp;' + powerLabel + '&nbsp;</a>';

    /* Assemble panel */
    panel.appendChild(header);
    panel.appendChild(ctxBar);
    panel.appendChild(messages);
    /* typing indicator lives inside the message list so appendMsg can
       insertBefore() it — appending it to the panel breaks every message */
    messages.appendChild(typingRow);
    panel.appendChild(bookBanner);
    panel.appendChild(suggestWrap);
    panel.appendChild(qualify);
    panel.appendChild(escalate);
    panel.appendChild(quick);
    panel.appendChild(inputRow);
    panel.appendChild(power);


    /* Exit-intent overlay */
    var exitOverlay = document.createElement("div");
    exitOverlay.id = "mc-exit-overlay";
    var exitCard = document.createElement("div");
    exitCard.id = "mc-exit-card";
    exitCard.innerHTML =
      '<button id="mc-exit-dismiss" aria-label="Close">\u00d7</button>' +
      '<h3>Before you go\u2026</h3>' +
      '<p>Have a quick question? Ask ' + AGENT_NAME + ' now or book a free 30-min discovery call with our team.</p>' +
      '<input id="mc-exit-input" type="text" placeholder="Type your question here\u2026" />' +
      '<div id="mc-exit-actions">' +
        '<button id="mc-exit-chat">Ask ' + AGENT_NAME + '</button>' +
        '<a id="mc-exit-book" href="' + BOOKING_URL + '" target="_blank" rel="noopener noreferrer">Book a Call</a>' +
      '</div>';
    exitOverlay.appendChild(exitCard);
    root.appendChild(exitOverlay);

    root.appendChild(fab);
    root.appendChild(panel);
    document.body.appendChild(root);

    /* ─── WIRING ─────────────────────────────────────────────── */

    function showEscalateForm(show) {
      escalate.classList.toggle("show", !!show);
      if (show) {
        var lead = getLead();
        if (lead.name) escName.value = lead.name;
        if (lead.email) escEmail.value = lead.email;
      }
    }

    function showStarterChips(cat) {
      showSuggestions(STARTER_CHIPS[cat] || STARTER_CHIPS.general);
    }

    function showInitialGreeting() {
      fetchWidgetContext().then(function(ctx) {
        if (messages.querySelector(".mc-bubble")) return;
        var cat = ctx.page_category || categoryFromPath();
        showCtxBar(cat);
        appendBot(ctx.opener || OPENERS[cat] || OPENERS.general);
        showSuggestions(ctx.starter_chips || STARTER_CHIPS[cat] || STARTER_CHIPS.general);
      });
    }

    function togglePanel() {
      isOpen = !isOpen;
      panel.classList.toggle("open", isOpen);
      fab.setAttribute("aria-expanded", isOpen ? "true" : "false");
      if (isOpen) {
        unread = 0;
        badge.classList.remove("show");
        fab.style.setProperty("--mc-pulse-play", "paused");
        fab.style.animation = "none";
        if (!sessionStorage.getItem(WIDGET_SEEN_KEY)) {
          sessionStorage.setItem(WIDGET_SEEN_KEY, "1");
          if (!messages.querySelector(".mc-bubble")) {
            track("widget_opened_no_message");
          } else {
            track("widget_opened");
          }
        }
        if (!messages.querySelector(".mc-bubble")) {
          showInitialGreeting();
        }
        setTimeout(function(){ input.focus(); }, 150);
        setTimeout(function(){ if (typeof _syncPanelToViewport === "function") _syncPanelToViewport(); }, 160);
      } else {
        if (messages.querySelector(".mc-row.user")) {
          track("conversation_abandoned");
        }
      }
    }

    fab.onclick    = togglePanel;
    minBtn.onclick = function(){ isOpen = true; togglePanel(); };
    closeBtn.onclick = function(){ isOpen = true; togglePanel(); };
    expandBtn.onclick = function(){
      var expanded = panel.classList.toggle("expanded");
      expandBtn.innerHTML = expanded ? ICON_COLLAPSE : ICON_EXPAND;
      expandBtn.title = expanded ? "Collapse" : "Expand";
      expandBtn.setAttribute("aria-label", expanded ? "Collapse chat window" : "Expand chat window");
      messages.scrollTop = messages.scrollHeight;
    };
    qSubmit.onclick = submitQualify;
    sendBtn.onclick = function(){ doSend(); };

    input.addEventListener("input", function() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 96) + "px";
    });
    input.addEventListener("keydown", function(e){
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSend(); }
    });

    /* Mobile keyboard handling: iOS/Android on-screen keyboards shrink the
       visualViewport but leave window.innerHeight unchanged, so a panel sized
       with vh units (see the @media(max-width:520px) rules above) can end up
       with its input row hidden behind the keyboard. While the panel is open
       on a narrow viewport, keep it pinned to the visible (post-keyboard)
       viewport instead of the full layout viewport. */
    if (window.visualViewport) {
      var _vv = window.visualViewport;
      var _syncPanelToViewport = function() {
        if (!isOpen || window.innerWidth > 520) {
          panel.style.removeProperty("bottom");
          panel.style.removeProperty("height");
          panel.style.removeProperty("max-height");
          return;
        }
        var keyboardInset = Math.max(0, window.innerHeight - _vv.height - _vv.offsetTop);
        panel.style.bottom = (keyboardInset + 8) + "px";
        panel.style.height = "auto";
        panel.style.maxHeight = Math.max(280, _vv.height - 16) + "px";
        messages.scrollTop = messages.scrollHeight;
      };
      _vv.addEventListener("resize", _syncPanelToViewport);
      _vv.addEventListener("scroll", _syncPanelToViewport);
      input.addEventListener("focus", function(){ setTimeout(_syncPanelToViewport, 60); });
      input.addEventListener("blur", function(){ setTimeout(_syncPanelToViewport, 60); });
    }

    document.addEventListener("keydown", function(e){
      if (e.key === "Escape" && isOpen) { isOpen = true; togglePanel(); }
    });

    window.addEventListener("beforeunload", function() {
      if (isOpen && messages.querySelector(".mc-row.user")) {
        track("conversation_abandoned");
      }
    });

    /* ─── HELPERS ─────────────────────────────────────────────── */

    function showCtxBar(cat) {
      var names = { ai_agents:"AI & Agents", case_studies:"Case Studies", services:"Services", pricing:"Pricing", about:"About", contact:"Contact", careers:"Careers", blog:"Blog", general:"Home" };
      var pg = document.getElementById("mc-ctx-page");
      if (pg) pg.textContent = names[cat] || "this page";
      ctxBar.classList.add("show");
    }

    function showToast(msg) {
      toast.textContent = msg;
      toast.classList.add("show");
      setTimeout(function(){ toast.classList.remove("show"); }, 2500);
    }

    function nowTS() {
      return new Date().toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" });
    }

    function appendMsg(role, html, text, msgId) {
      var row = document.createElement("div");
      row.className = "mc-row " + role;
      if (role === "bot") {
        var av = document.createElement("div");
        av.className = "mc-avatar-sm";
        if (AVATAR_URL) { var i2=document.createElement("img"); i2.src=AVATAR_URL; i2.alt=AGENT_NAME; av.appendChild(i2); }
        else av.innerHTML = ICON_BOT;
        row.appendChild(av);
      }
      var wrapper = document.createElement("div");
      var bubble = document.createElement("div");
      bubble.className = "mc-bubble";
      var content = document.createElement("div");
      content.className = "mc-content";
      if (role === "bot") {
        var aiHead = document.createElement("div");
        aiHead.className = "mc-ai-head";
        var aiTitle = document.createElement("span");
        aiTitle.className = "mc-ai-title";
        aiTitle.textContent = "✨ AI Response";
        var aiBadge = document.createElement("span");
        aiBadge.className = "mc-ai-badge";
        aiBadge.textContent = "Generated by AI";
        aiHead.appendChild(aiTitle);
        aiHead.appendChild(aiBadge);
        bubble.appendChild(aiHead);
      }
      if (html != null) content.innerHTML = html;
      else content.textContent = text || "";
      bubble.appendChild(content);
      wrapper.appendChild(bubble);
      var ts = document.createElement("div");
      ts.className = "mc-ts";
      ts.textContent = nowTS();
      wrapper.appendChild(ts);
      // Feedback buttons — only for bot messages with a message ID
      if (role === "bot" && msgId) {
        var fbRow = document.createElement("div");
        fbRow.className = "mc-feedback-row";
        var fbLabel = document.createElement("span");
        fbLabel.textContent = "Helpful?";
        var thumbUp = document.createElement("button");
        thumbUp.className = "mc-fb-btn"; thumbUp.title = "Helpful"; thumbUp.textContent = "\ud83d\udc4d";
        thumbUp.setAttribute("aria-label", "Mark response as helpful");
        var thumbDown = document.createElement("button");
        thumbDown.className = "mc-fb-btn"; thumbDown.title = "Not helpful"; thumbDown.textContent = "\ud83d\udc4e";
        thumbDown.setAttribute("aria-label", "Mark response as not helpful");
        fbRow.appendChild(fbLabel); fbRow.appendChild(thumbUp); fbRow.appendChild(thumbDown);
        wrapper.appendChild(fbRow);

        // Comment box \u2014 shown only after \ud83d\udc4e, hidden after submit
        var commentBox = document.createElement("div");
        commentBox.className = "mc-fb-comment-box";
        commentBox.style.cssText = "display:none;margin-top:6px;";
        var commentInput = document.createElement("textarea");
        commentInput.placeholder = "What went wrong? (optional)";
        commentInput.maxLength = 1000;
        commentInput.rows = 2;
        commentInput.style.cssText = "width:100%;border:1px solid #e2e8f0;border-radius:8px;padding:6px 9px;font-size:12px;font-family:inherit;resize:none;outline:none;box-sizing:border-box;";
        var commentSubmit = document.createElement("button");
        commentSubmit.textContent = "Send feedback";
        commentSubmit.style.cssText = "margin-top:5px;background:#0f172a;color:#fff;border:none;border-radius:7px;padding:5px 12px;font-size:12px;cursor:pointer;font-family:inherit;";
        commentBox.appendChild(commentInput);
        commentBox.appendChild(commentSubmit);
        wrapper.appendChild(commentBox);

        function submitFeedback(rating, comment) {
          thumbUp.disabled = true; thumbDown.disabled = true;
          thumbUp.classList.remove("active-pos"); thumbDown.classList.remove("active-neg");
          if (rating === 1) thumbUp.classList.add("active-pos");
          else thumbDown.classList.add("active-neg");
          commentBox.style.display = "none";
          fetch(FEEDBACK_URL, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: getSessionId(), message_id: msgId, rating: rating, comment: comment || "" }),
          }).catch(function() {});
          track(rating === 1 ? "feedback_positive" : "feedback_negative", msgId);
        }
        thumbUp.onclick = function() { submitFeedback(1, ""); };
        thumbDown.onclick = function() {
          // Show comment box before submitting \u2014 submit happens when user clicks Send or after 5s
          thumbDown.classList.add("active-neg");
          thumbUp.disabled = true;
          commentBox.style.display = "block";
          commentInput.focus();
          var _submitted = false;
          function sendNegative() {
            if (_submitted) return;
            _submitted = true;
            submitFeedback(-1, commentInput.value.trim());
          }
          commentSubmit.onclick = sendNegative;
          // Auto-submit after 30s if user doesn't click Send
          setTimeout(sendNegative, 30000);
        };
      }
      row.appendChild(wrapper);
      messages.insertBefore(row, typingRow);
      messages.scrollTop = messages.scrollHeight;
      return { row: row, bubble: bubble, content: content, wrapper: wrapper };
    }

    function appendBot(text, msgId) { return appendMsg("bot", renderBot(text), null, msgId || null); }
    function appendUser(text) { appendMsg("user", null, text); }

    function setTyping(on) {
      typingEl.style.display = on ? "flex" : "none";
      if (on) messages.scrollTop = messages.scrollHeight;
    }

    function setSending(on) {
      isSending = on;
      sendBtn.disabled = on;
      input.disabled = on;
    }

    function dismissSuggestions() {
      suggest.innerHTML = "";
      suggestWrap.classList.remove("show");
    }

    function showSuggestions(items) {
      suggest.innerHTML = "";
      var list = (items || []).filter(Boolean);
      if (!list.length) { suggestWrap.classList.remove("show"); return; }
      list.forEach(function(t){
        var c = document.createElement("button");
        c.className = "mc-schip";
        c.textContent = t;
        c.onclick = (function(msg){
          return function(){
            track("cta_clicked", msg === "Talk to our team" ? "human_escalation_chip" : "suggested_reply");
            if (msg === "Talk to our team") {
              showEscalateForm(true);
              return;
            }
            if (shouldOpenContactPage(msg)) {
              openBookingPage("suggested_reply");
              return;
            }
            doSend(msg);
          };
        })(t);
        suggest.appendChild(c);
      });
      suggestWrap.classList.add("show");
    }

    function dismissBookBanner() {
      try { localStorage.setItem(STORAGE_DISMISS_BOOK, "1"); } catch (e) {}
      bookBanner.classList.remove("show");
    }

    function showBookBanner(show, force) {
      /* `force` (instant_booking CTA): a hot lead we already know is exactly
         who should see the booking button — skip the known-lead suppression
         but still honour an explicit dismissal. */
      if (!force && hasKnownLead()) return;
      try {
        if (localStorage.getItem(STORAGE_DISMISS_BOOK)) return;
      } catch (e) {}
      bookBanner.classList.toggle("show", !!show);
    }

    function showQualifyForm(show) {
      qualify.classList.toggle("show", !!show);
    }

    bookClose.onclick = dismissBookBanner;
    suggestClose.onclick = dismissSuggestions;

    function renderCitations(citations) {
      if (!citations || !citations.length) return null;
      var wrap = document.createElement("div");
      wrap.className = "mc-citations";
      dedupeCitationsList(citations).slice(0, 3).forEach(function(c){
        var a = document.createElement("a");
        a.className = "mc-citation";
        a.href = c.source_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        var dot = document.createElement("span");
        dot.className = "mc-citation-dot";
        a.appendChild(dot);
        var label = (c.page_title || "").trim();
        if (!label && c.source_url) {
          try {
            var parts = new URL(c.source_url).pathname.split("/").filter(Boolean);
            label = parts.length ? parts[parts.length - 1].replace(/-/g, " ") : c.source_url;
          } catch (e) { label = c.source_url; }
        }
        a.appendChild(document.createTextNode(label || c.source_url || "Source"));
        wrap.appendChild(a);
      });
      return wrap;
    }

    function shouldUseCitationCards(citations) {
      var cat = categoryFromPath();
      if (cat === "case_studies") return true;
      return (citations || []).some(function(c) {
        var url = (c.source_url || "").toLowerCase();
        return (c.page_category || "") === "case_studies" || url.indexOf("case-stud") >= 0;
      });
    }

    function renderCitationCards(citations) {
      if (!citations || !citations.length) return null;
      var wrap = document.createElement("div");
      wrap.className = "mc-cite-cards";
      dedupeCitationsList(citations).slice(0, 2).forEach(function(c) {
        var card = document.createElement("a");
        card.className = "mc-cite-card";
        card.href = c.source_url || "#";
        card.target = "_blank";
        card.rel = "noopener noreferrer";
        var title = document.createElement("div");
        title.className = "mc-cite-card-title";
        title.textContent = (c.page_title || "").trim() || "Case study";
        var snippet = document.createElement("div");
        snippet.className = "mc-cite-card-snippet";
        snippet.textContent = (c.snippet || c.citation_text || "").trim();
        var link = document.createElement("div");
        link.className = "mc-cite-card-link";
        link.textContent = "Read more";
        card.appendChild(title);
        if (snippet.textContent) card.appendChild(snippet);
        card.appendChild(link);
        wrap.appendChild(card);
      });
      return wrap;
    }

    function renderSources(citations) {
      if (shouldUseCitationCards(citations)) return renderCitationCards(citations);
      return renderCitations(citations);
    }

    function renderMetaPills(stage, score, bubble) {
      if (!SHOW_DEBUG_SALES_STATE) return;
      if (!stage && !score) return;
      stage = STAGE_ALIAS[stage] || stage;
      var row = document.createElement("div");
      row.className = "mc-meta-row";
      if (stage && STAGE_LABEL[stage]) {
        var sp = document.createElement("span");
        sp.className = "mc-pill";
        sp.textContent = "Stage: " + STAGE_LABEL[stage];
        row.appendChild(sp);
      }
      if (score) {
        var sp2 = document.createElement("span");
        sp2.className = "mc-pill score";
        sp2.textContent = score.charAt(0).toUpperCase() + score.slice(1);
        sp2.style.background = SCORE_COLOR[score] || "#64748b";
        row.appendChild(sp2);
      }
      bubble.appendChild(row);
    }

    function handleData(data, bubble) {
      if (!data) return;
      if (data.session_id) localStorage.setItem(STORAGE_SESSION, data.session_id);
      if (data.lead_profile) {
        saveLead(data.lead_profile);
        var lp = data.lead_profile;
        if (lp.email && lp.name) suppressProactive();
      }
      var cits = renderSources(data.citations);
      if (cits && bubble) bubble.appendChild(cits);
      if (bubble) renderMetaPills(data.stage, data.lead_score, bubble);
      showSuggestions(data.suggested_replies);
      var cta = data.cta_type || "";
      if (cta === "instant_booking") {
        /* Hot lead ready to book — surface the booking banner regardless of
           whether we already know them; a button converts better than a link
           buried in the answer text. */
        suppressProactive();
        showBookBanner(true, true);
        track("cta_shown", "instant_booking");
      } else if (data.ready_for_booking && !hasKnownLead()) {
        suppressProactive();
        showBookBanner(true);
      } else if (!data.ready_for_booking) {
        showBookBanner(false);
      }
      if (cta === "human_handoff") {
        /* Visitor asked for a person (or is frustrated) — open the escalation
           form directly instead of hiding the path behind a chip. */
        showEscalateForm(true);
        track("cta_shown", "human_handoff");
      } else if (data.show_human_escalation) {
        var chips = (data.suggested_replies || []).slice();
        if (chips.indexOf("Talk to our team") < 0) chips.push("Talk to our team");
        showSuggestions(chips);
      }
      if (data.needs_contact_info) track("qualify_shown");
    }

    async function doSend(overrideText) {
      var text = (overrideText || input.value).trim();
      if (!text || isSending) return;
      input.value = ""; input.style.height = "auto";
      dismissSuggestions();
      showQualifyForm(false);

      appendUser(text);
      setTyping(true); setSending(true);

      var lead = getLead();
      var reqId = uuid();
      _updateScrollDepth(); // refresh scroll depth before send
      var payload = {
        message: text,
        session_id: getSessionId(),
        request_id: reqId,
        page_url: _pageUrl(),
        page_title: _pageTitle(),
        referrer: document.referrer || "",
        lead_consent: lead.lead_consent === true,
        expose_internal_sales_metadata: SHOW_DEBUG_SALES_STATE,
        lead_profile: {
          name: lead.name || "",
          email: lead.email || "",
          company: lead.company || "",
          project_need: lead.project_need || "",
          timeline: lead.timeline || "",
          budget_band: lead.budget_band || "",
        },
        visitor_meta: _captureVisitorMeta(), // Layer 1: passive fingerprint
        stream: USE_STREAM,
      };

      try {
        var url = USE_STREAM ? STREAM_URL : API_URL;
        var res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        if (res.status === 429) {
          setTyping(false);
          appendMsg("bot", null, "You're sending messages too quickly. Please wait a minute and try again.");
          setSending(false);
          return;
        }

        var data;
        if (USE_STREAM && res.body && (res.headers.get("content-type") || "").includes("text/event-stream")) {
          // Keep typing indicator on — consumeStream hides it on first token
          var parts2 = appendMsg("bot", "", null, reqId);
          parts2.bubble.style.display = "none"; // hidden until first token arrives
          data = await consumeStream(res, parts2.bubble, parts2.content);
        } else {
          data = await res.json();
          setTyping(false);
          if (!data.response) throw new Error("Empty response");
          var botReqId = (data.request_id || reqId);
          var parts3 = appendMsg("bot", renderBot(data.response), null, botReqId);
          handleData(data, parts3.bubble);
          data = null; // already handled
        }

        if (data) handleData(data, parts2 ? parts2.bubble : null);
      } catch (e) {
        setTyping(false);
        var stale = messages.querySelector(".mc-streaming");
        if (stale) stale.classList.remove("mc-streaming");
        appendMsg("bot", null, "Sorry, something went wrong. Please try again.");
      }

      setSending(false);
      if (!isOpen) { unread++; badge.textContent = unread; badge.classList.add("show"); }
    }

    async function consumeStream(res, bubble, content) {
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "", fullText = "", meta = null;
      var firstToken = true;

      // Debounce DOM text updates to avoid layout thrashing on fast token streams.
      // We accumulate tokens in fullText and only flush to the DOM every 40ms.
      var pendingFlush = false;
      function scheduleFlush() {
        if (pendingFlush) return;
        pendingFlush = true;
        setTimeout(function() {
          pendingFlush = false;
          // On first token: hide typing dots and reveal the bubble
          if (firstToken) {
            firstToken = false;
            setTyping(false);
            bubble.style.display = "";
            content.classList.add("mc-streaming");
          }
          content.innerHTML = renderBot(fullText);
          messages.scrollTop = messages.scrollHeight;
        }, 40);
      }

      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        var parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (var i = 0; i < parts.length; i++) {
          var part = parts[i];
          if (!part.startsWith("data:")) continue;
          var raw = part.slice(5).trim();
          if (raw === "[DONE]") continue;
          var ev;
          try { ev = JSON.parse(raw); } catch(e) { continue; }

          if (ev.type === "token" && ev.content) {
            fullText += ev.content;
            scheduleFlush();
          } else if (ev.type === "replace" && ev.response) {
            fullText = ev.response;
            setTyping(false); firstToken = false; bubble.style.display = "";
            content.innerHTML = renderBot(fullText);
            messages.scrollTop = messages.scrollHeight;
          } else if (ev.type === "done") {
            if (ev.response) { fullText = ev.response; }
            setTyping(false); firstToken = false; bubble.style.display = "";
            content.innerHTML = renderBot(fullText);
            messages.scrollTop = messages.scrollHeight;
            /* The meta event arrives before done — merge rather than clobber
               so cta_type / suggested_replies / citations survive. */
            meta = Object.assign({}, meta || {}, ev);
          } else if (ev.type === "meta" && ev.data) {
            meta = ev.data;
            if (ev.data.session_id) localStorage.setItem(STORAGE_SESSION, ev.data.session_id);
          } else if (!ev.type && ev.response) {
            fullText = ev.response;
            setTyping(false); firstToken = false; bubble.style.display = "";
            content.innerHTML = renderBot(fullText);
            messages.scrollTop = messages.scrollHeight;
            meta = ev;
          }
        }
      }
      // Final flush — ensure typing is hidden and bubble is visible
      setTyping(false);
      bubble.style.display = "";
      content.classList.remove("mc-streaming");
      if (fullText) content.innerHTML = renderBot(fullText);
      if (!meta) meta = { response: fullText };
      return meta;
    }

    function submitQualify() {
      var profile = {};
      Object.keys(qInputs).forEach(function(k){ profile[k] = qInputs[k].value.trim(); });
      profile.lead_consent = true;
      saveLead(profile);
      suppressProactive();
      showQualifyForm(false);
      track("qualify_submitted");
      showToast("Thanks! Sending…");
      var msg = profile.project_need
        ? "My name is " + (profile.name || "there") + " from " + (profile.company || "my company") + ". I'm working on: " + profile.project_need
        : "My name is " + (profile.name || "there") + " from " + (profile.company || "my company") + ".";
      doSend(msg);
    }

    escCancel.onclick = function(){ showEscalateForm(false); };
    escSubmit.onclick = async function(){
      var name = escName.value.trim();
      var email = escEmail.value.trim();
      var message = escMsg.value.trim();
      if (!name || !email || !message) {
        showToast("Please fill in all fields.");
        return;
      }
      saveLead({ name: name, email: email, lead_consent: true });
      suppressProactive();
      track("human_escalation_requested");
      try {
        var res = await fetch(ESCALATE_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: getSessionId(),
            name: name,
            email: email,
            message: message,
            page_url: _pageUrl(),
            lead_consent: true,
          }),
        });
        if (!res.ok) throw new Error("escalation failed");
        showEscalateForm(false);
        appendBot("We've received your message. A " + COMPANY + " team member will reach out within 1 business day.");
        showToast("Message sent!");
      } catch (e) {
        showToast("Could not send — please try again.");
      }
    };

    // ─── PROACTIVE TRIGGER (F2) ────────────────────────────────── //

    var PROACTIVE_OPENERS = {
      ai_agents:  "\ud83d\udc4b Building something with AI? I can walk you through Mobcoder AI's agentic solutions \u2014 ask me anything!",
      services:   "\ud83d\udc4b Looking for a development partner? Tell me what you're building and I'll share what's most relevant.",
    };

    function scheduleProactiveOpen() {
      if (!(AUTO_OPEN_DELAY > 0)) return;
      if (shouldSuppressProactive()) return;
      if (sessionStorage.getItem(AUTO_OPEN_KEY)) return;
      var cat = categoryFromPath();
      if (cat === "contact") return;
      if (PROACTIVE_PAGES.indexOf(cat) < 0) return;
      var badgeDelay = Math.max(0, AUTO_OPEN_DELAY - 20) * 1000;
      setTimeout(function(){
        if (!isOpen && !sessionStorage.getItem(AUTO_OPEN_KEY)) badge.classList.add("show");
      }, badgeDelay);
      setTimeout(function(){
        if (!isOpen && !sessionStorage.getItem(AUTO_OPEN_KEY)) {
          sessionStorage.setItem(AUTO_OPEN_KEY, "1");
          track("proactive_trigger_shown");
          if (!messages.querySelector(".mc-bubble")) {
            showInitialGreeting();
          }
          if (!isOpen) togglePanel();
        }
      }, AUTO_OPEN_DELAY * 1000);
    }

    // ─── EXIT INTENT (F3) ─────────────────────────────────────── //

    var exitInput  = exitCard.querySelector("#mc-exit-input");
    var exitChat   = exitCard.querySelector("#mc-exit-chat");
    var exitBook   = exitCard.querySelector("#mc-exit-book");
    var exitClose  = exitCard.querySelector("#mc-exit-dismiss");

    function showExitOverlay() {
      try { sessionStorage.setItem(EXIT_INTENT_KEY, "1"); } catch (e) {}
      track("exit_intent_shown");
      exitOverlay.classList.add("show");
      setTimeout(function(){ if (exitInput) exitInput.focus(); }, 100);
    }

    function hideExitOverlay() {
      exitOverlay.classList.remove("show");
    }

    exitClose.onclick = hideExitOverlay;
    exitOverlay.onclick = function(e) { if (e.target === exitOverlay) hideExitOverlay(); };

    exitChat.onclick = function() {
      var q = (exitInput ? exitInput.value.trim() : "");
      hideExitOverlay();
      if (!isOpen) { isOpen = false; togglePanel(); }
      if (q) doSend(q);
    };

    exitBook.onclick = function() {
      track("booking_cta_clicked", "exit_intent");
      hideExitOverlay();
    };

    document.addEventListener("mouseleave", function(e) {
      if (e.clientY > 5) return;
      if (isOpen) return;
      if (shouldSuppressProactive()) return;
      try { if (sessionStorage.getItem(EXIT_INTENT_KEY)) return; } catch (e) {}
      var cat = categoryFromPath();
      if (cat === "contact") return;
      if (PROACTIVE_PAGES.indexOf(cat) < 0) return;
      if (window.scrollY < 100 && !sessionStorage.getItem(WIDGET_SEEN_KEY)) return;
      showExitOverlay();
    });

    scheduleProactiveOpen();

    window.MobcoderChat = window.MobcoderChat || {};
    window.MobcoderChat.onPageChange = function(url) {
      if (url) window.MOBCODER_PAGE_URL = String(url);
      if (!isOpen) return;
      if (messages.querySelector(".mc-row.user")) return;
      messages.querySelectorAll(".mc-row.bot").forEach(function(row) { row.remove(); });
      dismissSuggestions();
      showInitialGreeting();
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
