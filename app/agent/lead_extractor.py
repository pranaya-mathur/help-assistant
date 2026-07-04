from __future__ import annotations

import re
from typing import Any

from app.agent.lead_intelligence import (
    enrich_profile_from_text,
    compute_lead_scoring,
    extract_industry,
)

# ─── Incremental passive extraction patterns ────────────────────────────────

# Company mention detection: "we at Acme", "I work at MobCoder", "from Stripe"
_COMPANY_MENTION_RE = re.compile(
    r"(?:"
    r"we\s+(?:at|are\s+at)\s+|"
    r"our\s+(?:company|startup|firm|organization|org)(?:\s+is|\s+called)?\s+|"
    r"i\s+(?:work|am)\s+(?:at|for|with)\s+|"
    r"(?:from|at)\s+(?:a\s+company\s+called\s+)?"
    r")"
    r"([A-Z][A-Za-z0-9&.,\'\-]{1,60})",
    re.I,
)

# Team / org size signals (map to team_size profile field)
_TEAM_SMALL_RE = re.compile(
    r"\b(?:small\s+team|just\s+(?:us|me\s+and\s+\w+)|(?:solo|bootstrap(?:ped)?)|"
    r"early.?stage|pre.?seed|(?:under\s+)?(?:5|ten|10|fifteen|15|20)\s+"
    r"(?:people|devs?|engineers?|employees?))\b",
    re.I,
)
_TEAM_MID_RE = re.compile(
    r"\b(?:series\s+[ab]|growth.?stage|mid.?size|scale.?up|"
    r"(?:50|100|150|200)\s+(?:people|employees?|engineers?))\b",
    re.I,
)
_TEAM_LARGE_RE = re.compile(
    r"\b(?:enterprise|large\s+(?:org|company|team)|fortune\s+\d+|"
    r"(?:500|1000|\d{4,})\s*\+?\s*(?:people|employees?)|"
    r"global\s+(?:team|company)|thousands?\s+of\s+(?:employees?|users?))\b",
    re.I,
)

# Technical seniority signals (infer role without asking)
_TECH_SENIOR_RE = re.compile(
    r"\b(?:architect|principal\s+engineer|staff\s+engineer|tech\s+lead|"
    r"vp\s+(?:of\s+)?(?:eng(?:ineering)?|product|tech)|"
    r"head\s+of\s+(?:eng(?:ineering)?|product|tech)|"
    r"senior\s+(?:dev(?:eloper)?|engineer)|"
    r"we\s+(?:use|run|deploy|built|have)\s+"
    r"(?:kubernetes|k8s|aws|gcp|azure|terraform|docker|kafka|redis|postgres|"
    r"microservices|grpc|graphql))\b",
    re.I,
)
_TECH_JUNIOR_RE = re.compile(
    r"\b(?:learning|beginner|just\s+started|first\s+(?:app|project|time)|"
    r"no\s+(?:backend|devops|cloud)\s+(?:experience|knowledge)|"
    r"don\'?t\s+(?:know|understand)\s+(?:how|much\s+about))\b",
    re.I,
)

# Qualification order: project context before contact details
LEAD_FIELDS_ORDER = ["project_need", "timeline", "budget_band", "name", "email", "company"]

# Lighter set when visitor only wants to book a call
BOOKING_FIELDS_ORDER = ["name", "email", "company"]

# Help MQL: contact only after rapport (see MIN_HELP_TURNS_BEFORE_QUALIFY)
HELP_FIELDS_ORDER = ["name", "email"]

FIELD_QUESTIONS = {
    "project_need": "What kind of project are you exploring (e.g. AI agents, mobile app, team augmentation)?",
    "timeline": "What timeline are you working toward?",
    "budget_band": "Do you have a rough budget range in mind (e.g. under $50k, $50k–150k, $150k+)?",
    "name": "May I have your name so our team can follow up?",
    "email": "What is the best email to reach you?",
    "company": "Which company are you with?",
}

# Prior user messages in session history before appending a qualify question.
# Current message is not in history yet, so 2 prior user turns = 3rd message qualifies.
MIN_HELP_TURNS_BEFORE_QUALIFY = 2
MIN_SALES_TURNS_BEFORE_QUALIFY = 2

# Once eligible, re-ask the qualify question at most every N turns instead of
# on every single reply — avoids nagging visitors who already saw the question
# and haven't answered it yet.
QUALIFY_REASK_COOLDOWN_TURNS = 3

# Stages in which qualification questions may be appended.
# discover / educate: too early — answer first, never interrogate.
# convert: lead already complete — no question needed.
# qualify: rapport established and fields are missing — fire the question.
_QUALIFY_ALLOWED_STAGES = frozenset({"qualify"})

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def extract_from_message(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    email_match = _EMAIL_RE.search(text)
    if email_match:
        found["email"] = email_match.group(0)

    lower = text.lower()
    if any(w in lower for w in ("ai agent", "agentic", "llm", "genai", "chatbot")):
        found.setdefault("project_need", "AI / agentic systems")
    elif any(w in lower for w in ("mobile app", "ios", "android")):
        found.setdefault("project_need", "Mobile application")
    elif any(w in lower for w in ("web app", "website", "portal")):
        found.setdefault("project_need", "Web application")

    for pattern in (r"\$?\s*\d+\s*k", r"under\s*\$?\s*\d+"):
        m = re.search(pattern, lower)
        if m:
            found.setdefault("budget_band", m.group(0))
            break

    return enrich_profile_from_text(found, text)


def _extract_company_mention(text: str) -> str:
    """Extract a company name from natural language patterns if present."""
    m = _COMPANY_MENTION_RE.search(text)
    if not m:
        return ""
    candidate = m.group(1).strip().rstrip(".,;:")
    # Reject very short or generic tokens
    if len(candidate) < 2 or candidate.lower() in {
        "a", "an", "the", "my", "our", "your", "their", "this", "that", "it",
        "us", "you", "them", "here", "there",
    }:
        return ""
    return candidate


def _infer_team_size(text: str) -> str:
    """Return 'small', 'mid', 'large', or '' based on conversation cues."""
    if _TEAM_LARGE_RE.search(text):
        return "large"
    if _TEAM_MID_RE.search(text):
        return "mid"
    if _TEAM_SMALL_RE.search(text):
        return "small"
    return ""


def _infer_tech_seniority(text: str) -> str:
    """Return 'senior', 'junior', or '' based on vocabulary signals."""
    if _TECH_SENIOR_RE.search(text):
        return "senior"
    if _TECH_JUNIOR_RE.search(text):
        return "junior"
    return ""


def extract_incremental(text: str, existing_profile: dict[str, Any]) -> dict[str, Any]:
    """Run passive extraction on a single user turn and return new signals.

    Does NOT overwrite fields already confirmed in existing_profile.
    Safe to call on every user turn — merge result with merge_profiles().
    """
    signals: dict[str, Any] = {}

    # Company name (only if not already captured)
    if not existing_profile.get("company"):
        company = _extract_company_mention(text)
        if company:
            signals["company"] = company

    # Team size (supplemental signal — stored separately for scoring)
    if not existing_profile.get("team_size"):
        team_size = _infer_team_size(text)
        if team_size:
            signals["team_size"] = team_size

    # Tech seniority (helps score without asking role)
    if not existing_profile.get("tech_seniority"):
        seniority = _infer_tech_seniority(text)
        if seniority:
            signals["tech_seniority"] = seniority

    # Industry from text (only if not already set to a specific value)
    existing_industry = str(existing_profile.get("industry") or "")
    if not existing_industry or existing_industry == "unknown":
        industry = extract_industry(text)
        if industry and industry != "unknown":
            signals["industry"] = industry

    # Standard enrichment from existing extract_from_message logic (email, project_need, budget)
    base = extract_from_message(text)
    for k, v in base.items():
        if not existing_profile.get(k) and v:
            signals[k] = v

    return signals


def merge_profiles(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Merge two lead profiles, keeping non-empty values; update wins on conflict."""
    merged = dict(base)
    for k, v in update.items():
        if isinstance(v, bool):
            merged[k] = v
        elif v and str(v).strip():
            merged[k] = str(v).strip()
    return merged


def _field_order_for_intent(intent: str) -> list[str]:
    if intent == "booking":
        return BOOKING_FIELDS_ORDER
    if intent == "sales":
        return LEAD_FIELDS_ORDER
    if intent == "help":
        return HELP_FIELDS_ORDER
    return []


def has_contact_capture(profile: dict[str, Any]) -> bool:
    """True when visitor has shared name + email (minimum website lead capture)."""
    return bool((profile.get("name") or "").strip() and (profile.get("email") or "").strip())


def get_missing_lead_fields(profile: dict[str, Any], intent: str) -> list[str]:
    order = _field_order_for_intent(intent)
    if not order:
        return []
    return [f for f in order if not profile.get(f)]


def count_user_turns(history: list[dict[str, str]] | None) -> int:
    if not history:
        return 0
    return sum(1 for m in history if m.get("role") == "user")


def should_append_qualification(
    intent: str,
    history: list[dict[str, str]] | None,
    profile: dict[str, Any],
    stage: str = "",
) -> bool:
    """Return True if a soft lead-qualification question should be appended."""
    if intent == "booking":
        return bool(get_missing_lead_fields(profile, "booking"))

    # After name + email are captured, stop appending qualify questions (help-first UX).
    if has_contact_capture(profile):
        return False

    if stage not in _QUALIFY_ALLOWED_STAGES:
        return False

    missing = get_missing_lead_fields(profile, intent)
    if not missing:
        return False

    if intent == "sales":
        threshold = MIN_SALES_TURNS_BEFORE_QUALIFY
    elif intent == "help":
        threshold = MIN_HELP_TURNS_BEFORE_QUALIFY
    else:
        return False

    turns = count_user_turns(history)
    if turns < threshold:
        return False
    # Ask once at the threshold turn, then back off — re-ask only every
    # QUALIFY_REASK_COOLDOWN_TURNS turns rather than on every single reply.
    return (turns - threshold) % QUALIFY_REASK_COOLDOWN_TURNS == 0


def compute_lead_score(
    profile: dict[str, Any],
    intent: str,
    *,
    history: list[dict[str, str]] | None = None,
    page_url: str = "",
    user_query: str = "",
) -> str:
    """hot | warm | cold based on qualification signals."""
    return compute_lead_scoring(
        profile,
        intent,
        history=history,
        page_url=page_url,
        user_query=user_query,
    )["lead_score"]


def compute_meeting_readiness(
    profile: dict[str, Any],
    intent: str,
    *,
    history: list[dict[str, str]] | None = None,
    page_url: str = "",
    user_query: str = "",
) -> str:
    return compute_lead_scoring(
        profile,
        intent,
        history=history,
        page_url=page_url,
        user_query=user_query,
    )["meeting_readiness"]


def compute_lead_score_numeric(
    profile: dict[str, Any],
    intent: str,
    *,
    history: list[dict[str, str]] | None = None,
    page_url: str = "",
    user_query: str = "",
) -> int:
    return int(
        compute_lead_scoring(
            profile,
            intent,
            history=history,
            page_url=page_url,
            user_query=user_query,
        )["lead_score_numeric"]
    )


def is_lead_complete(profile: dict[str, Any], intent: str) -> bool:
    if intent == "booking":
        return not get_missing_lead_fields(profile, "booking")
    if intent == "sales":
        return not get_missing_lead_fields(profile, "sales")
    if intent == "help":
        return not get_missing_lead_fields(profile, "help")
    return False


def next_qualification_question(missing: list[str]) -> str:
    if not missing:
        return ""
    return FIELD_QUESTIONS.get(
        missing[0],
        "Could you share a bit more about your project so we can point you to the right team?",
    )


# ─── Progressive profiling questions (Layer 4) ──────────────────────────────
# Contextual questions keyed by (missing_field, project_type_or_intent).
# More natural than cold form-field prompts.

_PROGRESSIVE_QUESTIONS: dict[tuple[str, str], str] = {
    # project_need variants
    ("project_need", "sales"): (
        "Is this more about automating an internal workflow, or building something customer-facing?"
    ),
    ("project_need", "ai_agent"): (
        "Are you looking to build an AI agent from scratch, or augment an existing product with AI?"
    ),
    ("project_need", "mobile"): (
        "Is this a consumer app or an internal tool for your team?"
    ),
    # timeline variants
    ("timeline", "ai_agent"): (
        "Are you looking to have this in production within a few months, or is this more exploratory?"
    ),
    ("timeline", "sales"): (
        "What's driving the urgency — a product launch, a board deadline, or something else?"
    ),
    # budget variants
    ("budget_band", "sales"): (
        "Do you have a rough investment range in mind, or are you still scoping that out?"
    ),
    # contact variants
    ("email", "booking"): (
        "What email should I have the team reach out on to confirm the time?"
    ),
    ("email", "sales"): (
        "What's the best email for our solutions team to follow up with you?"
    ),
    ("name", "sales"): (
        "Who from your side would be joining the discovery call?"
    ),
    ("company", "sales"): (
        "Which company are you building this for?"
    ),
    ("timeline", "mobile_app"): "Do you have a launch date or milestone in mind for the app?",
    ("timeline", "sales"): "Is there a particular quarter or deadline you're working toward?",
    ("budget_band", "sales"): (
        "To make sure we scope this right — are you thinking of a smaller focused engagement, "
        "or a larger ongoing partnership?"
    ),
    ("budget_band", "ai_agent"): (
        "Are you looking at a fixed-scope project or an ongoing development retainer?"
    ),
    ("company", "booking"): (
        "And what company should I mention to the team when they reach out?"
    ),
    ("name", "booking"): "Who should we address the follow-up to?",
    ("name", "sales"): "I'd love to put a name to our conversation — who am I speaking with?",
    ("email", "booking"): (
        "What's the best email for our team to send calendar details to?"
    ),
    ("email", "sales"): (
        "What email should we use if our team wants to follow up with a proposal?"
    ),
}


_PROGRESSIVE_FALLBACKS: dict[str, str] = {
    "project_need": "What kind of project or challenge are you working on?",
    "timeline": "What timeline are you thinking for this?",
    "budget_band": "Are you working with a specific budget range in mind?",
    "company": "Which organization is this for?",
    "name": "Who am I speaking with?",
    "email": "What's the best email to reach you on?",
}


def next_progressive_question(missing: list[str], intent: str, project_type: str = "") -> str:
    """Return a contextual, conversational question for the first missing field.

    Prefers a project_type-specific variant, falls back to intent variant,
    then to a generic fallback. Returns '' if nothing is missing.
    """
    if not missing:
        return ""
    field = missing[0]
    lookup_type = project_type if project_type and project_type != "unknown" else intent
    question = (
        _PROGRESSIVE_QUESTIONS.get((field, lookup_type))
        or _PROGRESSIVE_QUESTIONS.get((field, intent))
        or _PROGRESSIVE_FALLBACKS.get(field)
        or FIELD_QUESTIONS.get(field, "")
    )
    return question
