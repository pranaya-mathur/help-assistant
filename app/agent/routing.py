from __future__ import annotations

from typing import Any

from app.agent.lead_intelligence import is_frustrated, requests_human

CTA_INSTANT_BOOKING = "instant_booking"
CTA_QUALIFY = "qualify"
CTA_HUMAN_HANDOFF = "human_handoff"
CTA_EDUCATE = "educate"

# Value-component threshold above which a hot lead deserves human eyes before
# automated follow-up (mid budget signal or better — see compute_lead_scoring).
_HIGH_VALUE_THRESHOLD = 20


def _qualification(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state.get("qualification_score") or {})


def decide_cta(state: dict[str, Any]) -> str:
    """Pick the strongest CTA the visitor has earned this turn."""
    query = str(state.get("user_query") or "")
    if requests_human(query) or is_frustrated(query):
        return CTA_HUMAN_HANDOFF

    readiness = str(state.get("meeting_readiness") or "not_ready")
    bucket = str(_qualification(state).get("bucket") or state.get("lead_score") or "cold")
    if readiness == "booking_requested" or (bucket == "hot" and readiness == "ready"):
        return CTA_INSTANT_BOOKING
    if bucket == "warm" or str(state.get("stage") or "") in {"qualify", "convert"}:
        return CTA_QUALIFY
    return CTA_EDUCATE


def needs_human_review(state: dict[str, Any]) -> bool:
    """Hot, contactable leads with real value signals get a human check
    before any automated deal/booking follow-up fires."""
    qual = _qualification(state)
    profile = dict(state.get("lead_profile") or {})
    if not profile.get("email"):
        return False
    if str(qual.get("bucket") or "") != "hot":
        return False
    enterprise = any("enterprise" in r or "compliance" in r for r in qual.get("reasons") or [])
    return enterprise or int(qual.get("value") or 0) >= _HIGH_VALUE_THRESHOLD


def build_human_review_summary(state: dict[str, Any]) -> str:
    """Compact summary a salesperson can act on without reading the transcript."""
    profile = dict(state.get("lead_profile") or {})
    qual = _qualification(state)
    lines = [
        f"Lead: {profile.get('name') or 'unknown'}"
        f" ({profile.get('email') or 'no email'})"
        f" — {profile.get('company') or 'company unknown'}",
        f"Score: {qual.get('total', 0)}/100 ({qual.get('bucket', 'cold')})"
        f" — fit {qual.get('fit', 0)}, intent {qual.get('intent', 0)},"
        f" value {qual.get('value', 0)}",
    ]
    if profile.get("project_need"):
        lines.append(f"Need: {profile['project_need']}")
    details = ", ".join(
        f"{field}: {profile[field]}"
        for field in ("project_type", "timeline", "budget_band", "role", "industry")
        if profile.get(field) and profile.get(field) != "unknown"
    )
    if details:
        lines.append(details)
    reasons = qual.get("reasons") or []
    if reasons:
        lines.append("Signals: " + "; ".join(reasons[:6]))
    return "\n".join(lines)


def decide_routing(state: dict[str, Any]) -> dict[str, Any]:
    """Return routing fields to merge into agent state."""
    review = needs_human_review(state)
    return {
        "cta_type": decide_cta(state),
        "needs_human_review": review,
        "human_review_summary": build_human_review_summary(state) if review else "",
    }
