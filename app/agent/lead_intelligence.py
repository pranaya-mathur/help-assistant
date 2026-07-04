from __future__ import annotations

import re
from typing import Any

PROJECT_TYPES = frozenset({
    "ai_agent",
    "rag_chatbot",
    "mobile_app",
    "web_app",
    "enterprise_software",
    "healthcare_fintech",
    "staff_augmentation",
    "maintenance_support",
    "cloud_devops",
    "ecommerce",
    "unknown",
})

ROLES = frozenset({
    "founder",
    "ceo",
    "cto",
    "coo",
    "product_manager",
    "engineering_manager",
    "marketing_manager",
    "consultant",
    "unknown",
})

INDUSTRIES = frozenset({
    "healthcare",
    "fintech",
    "ecommerce",
    "saas",
    "logistics",
    "education",
    "real_estate",
    "retail",
    "manufacturing",
    "other",
    "unknown",
})

DECISION_MAKER_ROLES = frozenset({
    "founder",
    "ceo",
    "cto",
    "coo",
})

_PROJECT_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("ai_agent", (
        r"\bai agent\b", r"\blanggraph\b", r"\bautonomous workflow\b",
        r"\bagentic\b", r"\bllm agent\b",
    )),
    ("rag_chatbot", (
        r"\brag\b", r"\binternal docs\b", r"\bknowledge bot\b",
        r"\bdocument q&a\b", r"\bdocument qa\b", r"\bknowledge base chat\b",
    )),
    ("mobile_app", (
        r"\bios\b", r"\bandroid\b", r"\bflutter\b", r"\breact native\b",
        r"\bmobile app\b",
    )),
    ("web_app", (
        r"\breact\b", r"\bnext\.js\b", r"\bnextjs\b", r"\bweb portal\b",
        r"\bdashboard\b", r"\bsaas\b", r"\bweb app\b",
    )),
    ("enterprise_software", (
        r"\berp\b", r"\benterprise workflow\b", r"\binternal platform\b",
        r"\benterprise software\b",
    )),
    ("healthcare_fintech", (
        r"\bhipaa\b", r"\bhealthcare\b", r"\bfintech\b", r"\bbanking\b",
        r"\bcompliance\b", r"\bregulated\b",
    )),
    ("staff_augmentation", (
        r"\bhire developers\b", r"\bdedicated team\b", r"\bstaff augmentation\b",
        r"\bteam augmentation\b", r"\boutsource developers\b",
    )),
    ("maintenance_support", (
        r"\bmaintain\b", r"\bsupport\b", r"\bbug fix", r"\bexisting app\b",
        r"\blegacy app\b",
    )),
    ("cloud_devops", (
        r"\baws\b", r"\bgcp\b", r"\bazure\b", r"\bdevops\b",
        r"\bcloud deployment\b", r"\bkubernetes\b",
    )),
    ("ecommerce", (
        r"\be-?commerce\b", r"\bmarketplace\b", r"\bshopify\b", r"\bretail platform\b",
    )),
]

_ROLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("founder", (r"\bfounder\b", r"\bco-founder\b", r"\bcofounder\b")),
    ("ceo", (r"\bceo\b", r"\bchief executive\b")),
    ("cto", (r"\bcto\b", r"\bchief technology officer\b")),
    ("coo", (r"\bcoo\b", r"\bchief operating officer\b")),
    ("product_manager", (r"\bproduct manager\b", r"\bpm\b")),
    ("engineering_manager", (r"\bengineering manager\b", r"\beng manager\b", r"\bvp engineering\b")),
    ("marketing_manager", (r"\bmarketing manager\b", r"\bcmo\b")),
    ("consultant", (r"\bconsultant\b", r"\badvisor\b")),
]

_INDUSTRY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("healthcare", (r"\bhealthcare\b", r"\bmedtech\b", r"\bmedical\b", r"\bhipaa\b")),
    ("fintech", (r"\bfintech\b", r"\bbanking\b", r"\bfinancial services\b", r"\bpayments\b")),
    ("ecommerce", (r"\be-?commerce\b", r"\bonline store\b", r"\bmarketplace\b")),
    ("saas", (r"\bsaas\b", r"\bsoftware company\b", r"\bb2b software\b")),
    ("logistics", (r"\blogistics\b", r"\bsupply chain\b", r"\bfleet\b")),
    ("education", (r"\beducation\b", r"\bedtech\b", r"\buniversity\b", r"\bschool\b")),
    ("real_estate", (r"\breal estate\b", r"\bproptech\b", r"\bproperty\b")),
    ("retail", (r"\bretail\b", r"\bstore chain\b")),
    ("manufacturing", (r"\bmanufacturing\b", r"\bindustrial\b", r"\bfactory\b")),
]

_TIMELINE_URGENT_RE = re.compile(
    r"\b(?:asap|urgent|immediately|this month|next month|"
    r"\d+\s*(?:weeks?|days?)|q[1-4]|within\s+\d+)\b",
    re.I,
)
_BUDGET_HIGH_RE = re.compile(
    r"\b(?:\$?\s*(?:1[5-9]\d|[2-9]\d{2}|[1-9]\d{3,})\s*k|\$?\s*1[5-9]\d{4,}|"
    r"150k\+|enterprise|six figures|500k|million)\b",
    re.I,
)
_BUDGET_MID_RE = re.compile(
    r"\b(?:\$?\s*(?:5\d|6\d|7\d|8\d|9\d|1[0-4]\d)\s*k|\$?\s*[5-9]\d{4,}|"
    r"50k|75k|100k|125k)\b",
    re.I,
)
_BUDGET_LOW_RE = re.compile(
    r"\b(?:\$?\s*(?:[1-4]?\d)\s*k|\$?\s*[1-4]\d{3,}|under\s*50k|small\s*budget)\b",
    re.I,
)
_TIMELINE_MID_RE = re.compile(
    r"\b(?:3\s*months?|6\s*months?|this\s*quarter|next\s*quarter|q[1-4])\b",
    re.I,
)
_HUMAN_ESCALATION_RE = re.compile(
    r"\b(?:speak|talk|chat)\s+(?:with|to)\s+(?:an?\s+)?(?:real\s+|actual\s+|live\s+)?"
    r"(?:human|person|someone|agent|rep(?:resentative)?|your\s+team|sales)\b"
    r"|\b(?:connect|transfer|put)\s+me\s+(?:to|through|with)\b"
    r"|\bget\s+me\s+(?:an?\s+)?(?:real\s+|actual\s+|live\s+)?(?:human|person|someone|agent)\b"
    r"|\b(?:real|actual|live)\s+(?:human|person|agent)\b"
    r"|\b(?:reach|contact)\s+(?:out\s+to\s+)?(?:someone|a\s+person|your\s+team|a\s+human|sales|support)\b"
    r"|\bhuman\s+support\b|\blive\s+agent\b|\bnot\s+a\s+bot\b"
    r"|\brather\s+email\b|\bescalate\s+(?:this|me|to)\b",
    re.I,
)
_FRUSTRATION_RE = re.compile(
    r"\buseless\b|\bnot\s+help(?:ing|ful)\b|\bwaste\s+of\s+time\b|\bvague\s+answers?\b"
    r"|\byou\s+keep\s+(?:giving|repeating|saying)\b|\basked\s+(?:you\s+)?(?:twice|already|before)\b"
    r"|\bfrustrat\w+\b|\bgoing\s+in\s+circles\b|\bnot\s+answering\b|\bsame\s+(?:vague\s+)?answer\b"
    r"|\bstupid\s+bot\b|\bthis\s+bot\s+(?:is|sucks)\b",
    re.I,
)
_BOOKING_RE = re.compile(
    r"\b(?:book(?:ing)?|schedule|calendly|discovery call|demo call|talk to sales)\b",
    re.I,
)
_ENTERPRISE_RE = re.compile(
    r"\b(?:enterprise|global|fortune|compliance|hipaa|soc\s*2|regulated)\b",
    re.I,
)
_HIGH_INTENT_PAGES = ("contact", "pricing", "services", "ai_agents")


def _first_match(text: str, rules: list[tuple[str, tuple[str, ...]]]) -> str:
    for label, patterns in rules:
        for pattern in patterns:
            if re.search(pattern, text, re.I):
                return label
    return ""


def classify_project_type(text: str, existing: str = "") -> str:
    if existing and existing in PROJECT_TYPES and existing != "unknown":
        return existing
    if not text.strip():
        return existing or "unknown"
    match = _first_match(text.lower(), _PROJECT_TYPE_RULES)
    return match or existing or "unknown"


def extract_role(text: str, existing: str = "") -> str:
    if existing and existing in ROLES and existing != "unknown":
        return existing
    match = _first_match(text.lower(), _ROLE_RULES)
    return match or existing or "unknown"


def extract_industry(text: str, existing: str = "") -> str:
    if existing and existing in INDUSTRIES and existing != "unknown":
        return existing
    match = _first_match(text.lower(), _INDUSTRY_RULES)
    return match or existing or "unknown"


def infer_decision_maker(role: str, text: str = "", existing: bool | None = None) -> bool:
    if existing is True:
        return True
    if role in DECISION_MAKER_ROLES:
        return True
    lower = text.lower()
    if re.search(r"\bdecision maker\b|\bfinal say\b|\bi approve\b|\bmy budget\b", lower):
        return True
    return bool(existing)


def enrich_profile_from_text(profile: dict[str, Any], text: str) -> dict[str, Any]:
    """Apply deterministic role/industry/project_type extraction to a profile."""
    merged = dict(profile)
    merged["project_type"] = classify_project_type(
        text, str(merged.get("project_type") or "")
    )
    merged["role"] = extract_role(text, str(merged.get("role") or ""))
    merged["industry"] = extract_industry(text, str(merged.get("industry") or ""))
    merged["decision_maker"] = infer_decision_maker(
        merged["role"],
        text,
        merged.get("decision_maker") if "decision_maker" in merged else None,
    )
    return merged


def _timeline_is_urgent(timeline: str) -> bool:
    if not timeline:
        return False
    lower = timeline.lower()
    if _TIMELINE_URGENT_RE.search(lower):
        return True
    week_match = re.search(r"(\d+)\s*weeks?", lower)
    if week_match and int(week_match.group(1)) <= 8:
        return True
    return False


def _page_category_from_url(page_url: str) -> str:
    if not page_url:
        return "general"
    lower = page_url.lower()
    if "/contact" in lower:
        return "contact"
    if "/pricing" in lower or "price" in lower:
        return "pricing"
    if "/services" in lower or "/ai" in lower:
        return "services"
    return "general"


def compute_lead_scoring(
    profile: dict[str, Any],
    intent: str,
    *,
    history: list[dict[str, str]] | None = None,
    page_url: str = "",
    user_query: str = "",
) -> dict[str, Any]:
    """Return numeric ICP fit score, label, meeting readiness, and the
    fit/intent/value decomposition with human-readable reasons."""
    fit = 0
    intent_score = 0
    value = 0
    reasons: list[str] = []
    text_blob = " ".join(
        part
        for part in (
            user_query,
            str(profile.get("project_need") or ""),
            str(profile.get("timeline") or ""),
            str(profile.get("budget_band") or ""),
            " ".join(
                m.get("content", "")
                for m in (history or [])
                if m.get("role") == "user"
            ),
        )
        if part
    ).lower()

    project_type = str(profile.get("project_type") or "unknown")
    if profile.get("project_need"):
        fit += 20
        reasons.append("described a concrete project need (+20 fit)")
    if project_type and project_type != "unknown":
        fit += 10
        reasons.append(f"project type identified: {project_type} (+10 fit)")

    timeline = str(profile.get("timeline") or "")
    if _timeline_is_urgent(timeline) or _TIMELINE_URGENT_RE.search(text_blob):
        intent_score += 30
        reasons.append("urgent timeline (+30 intent)")
    elif _TIMELINE_MID_RE.search(timeline) or _TIMELINE_MID_RE.search(text_blob):
        intent_score += 15
        reasons.append("near-term timeline (+15 intent)")

    budget = str(profile.get("budget_band") or "")
    if _BUDGET_HIGH_RE.search(budget) or _BUDGET_HIGH_RE.search(text_blob):
        value += 30
        reasons.append("high budget signal (+30 value)")
    elif _BUDGET_MID_RE.search(budget) or _BUDGET_MID_RE.search(text_blob):
        value += 20
        reasons.append("mid budget signal (+20 value)")
    elif _BUDGET_LOW_RE.search(budget) or _BUDGET_LOW_RE.search(text_blob):
        value += 5
        reasons.append("low budget signal (+5 value)")

    role = str(profile.get("role") or "unknown")
    if role in DECISION_MAKER_ROLES:
        fit += 20
        reasons.append(f"decision-maker role: {role} (+20 fit)")
    elif role in {"product_manager", "engineering_manager", "marketing_manager"}:
        fit += 10
        reasons.append(f"influencer role: {role} (+10 fit)")

    if profile.get("email") and profile.get("name"):
        intent_score += 10
        reasons.append("shared name and email (+10 intent)")
    elif profile.get("email"):
        intent_score += 5
        reasons.append("shared email (+5 intent)")

    booking_requested = intent == "booking" or bool(_BOOKING_RE.search(text_blob))
    if booking_requested:
        intent_score += 20
        reasons.append("asked to book a call (+20 intent)")
    elif intent == "sales":
        intent_score += 10
        reasons.append("sales-oriented conversation (+10 intent)")

    if _ENTERPRISE_RE.search(text_blob) or project_type in {"enterprise_software", "healthcare_fintech"}:
        value += 5
        reasons.append("enterprise/compliance signals (+5 value)")

    page_cat = _page_category_from_url(page_url)
    if page_cat in _HIGH_INTENT_PAGES:
        intent_score += 5
        reasons.append(f"browsing high-intent page: {page_cat} (+5 intent)")

    score = fit + intent_score + value

    if intent == "help":
        if profile.get("email") and profile.get("name"):
            score = max(score, 45)
        elif profile.get("email") or profile.get("name"):
            score = max(score, 35)
        else:
            score = min(score, 30)
            reasons.append("help-mode conversation without contact info (capped at 30)")

    score = max(0, min(100, score))

    if score >= 80:
        label = "hot"
    elif score >= 40:
        label = "warm"
    else:
        label = "cold"

    if booking_requested:
        readiness = "booking_requested"
    elif score >= 80 or (label == "hot" and profile.get("email")):
        readiness = "ready"
    elif score >= 45 or (profile.get("email") and profile.get("project_need")):
        readiness = "maybe_ready"
    else:
        readiness = "not_ready"

    return {
        "lead_score_numeric": score,
        "lead_score": label,
        "meeting_readiness": readiness,
        "qualification_score": {
            "fit": fit,
            "intent": intent_score,
            "value": value,
            "total": score,
            "bucket": label,
            "reasons": reasons,
        },
    }


# Which scoring component each qualification question feeds. Contact fields
# (name/email/company) are deliberately absent — their position in the intent
# field order is a capture-flow decision, not a scoring one.
_QUAL_FIELD_COMPONENT = {
    "project_need": "fit",
    "timeline": "intent",
    "budget_band": "value",
}


def prioritize_missing_fields(
    missing: list[str],
    qualification_score: dict[str, Any] | None,
) -> list[str]:
    """Reorder qualification fields so the weakest scoring dimension is probed
    first (e.g. value=0 → ask budget before timeline). Contact fields keep
    their original slots; ties keep the intent-defined order."""
    qual = qualification_score or {}
    scorable = [f for f in missing if f in _QUAL_FIELD_COMPONENT]
    if len(scorable) < 2 or not qual:
        return list(missing)
    ranked = sorted(
        scorable,
        key=lambda f: int(qual.get(_QUAL_FIELD_COMPONENT[f]) or 0),
    )
    it = iter(ranked)
    return [next(it) if f in _QUAL_FIELD_COMPONENT else f for f in missing]


def requests_human(query: str) -> bool:
    """Visitor explicitly asked for a person — hand off, don't pitch."""
    return bool(_HUMAN_ESCALATION_RE.search(query or ""))


def is_frustrated(query: str) -> bool:
    """Visitor is venting — drop follow-up questions and surface the human path."""
    return bool(_FRUSTRATION_RE.search(query or ""))


def should_offer_human_escalation(state: dict[str, Any]) -> bool:
    query = str(state.get("user_query") or "")
    if requests_human(query) or is_frustrated(query):
        return True
    intent = str(state.get("intent") or "")
    stage = str(state.get("stage") or "")
    return intent in {"sales", "booking"} and stage in {"qualify", "convert"}
