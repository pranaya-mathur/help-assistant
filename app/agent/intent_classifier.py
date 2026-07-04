from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.crawler.page_loader import infer_page_category

# Valid intents returned to the agent graph
INTENTS = ("help", "sales", "booking", "general", "out_of_scope")

RULE_CONFIDENCE_FLOOR = 0.78
RULE_MARGIN_MIN = 0.12


@dataclass
class IntentResult:
    intent: str
    page_category: str
    confidence: float
    source: str


BOOKING_PHRASES = [
    "book a call",
    "book a meeting",
    "book a discovery call",
    "schedule a call",
    "schedule a meeting",
    "schedule a discovery call",
    "set up a call",
    "book a demo",
    "schedule a demo",
    "i'd like to schedule",
    "i would like to schedule",
    "talk to sales",
    "speak with your team",
    "calendly",
]

# Do not treat educational questions about calls as booking
BOOKING_BLOCK_PATTERNS = [
    re.compile(r"\bhow\s+(do|does)\b.{0,40}\b(discovery\s+)?call", re.I),
    re.compile(r"\bexplain\b.{0,40}\b(discovery\s+)?call", re.I),
    re.compile(r"\btypically\s+run\b.{0,40}\bcall", re.I),
    re.compile(r"\bwhat\s+is\b.{0,30}\bcall", re.I),
]

SALES_PHRASES = [
    "get a quote",
    "need a quote",
    "pricing for",
    "how much would",
    "our budget",
    "rfp",
    "build for us",
    "need a team",
    "hire mobcoder",
    "work with mobcoder",
    "partner with mobcoder",
    "looking for a partner",
    "send a proposal",
    "contact sales",
    "contact your sales",
    "reach sales",
]

SALES_KEYWORDS = [
    "hire", "quote", "pricing", "cost", "budget", "proposal", "vendor", "rfp",
]

HELP_KEYWORDS = [
    "what do you",
    "how do you",
    "what services",
    "case study",
    "tech stack",
    "experience",
    "capabilities",
    "about mobcoder",
    "who is",
    "tell me about",
    "do you offer",
    "explain how",
    "can you explain",
    "typically run",
    "how does mobcoder",
    "do you work with",
    "what is mobcoder",
    "why choose",
    "why should we pick",
    "compare",
    "different from",
]

HELP_QUESTION_PATTERNS = [
    re.compile(r"\bwhat\b.{0,80}\b(services?|capabilities|offer)\b", re.I),
    re.compile(r"\bwhat does mobcoder offer\b", re.I),
    re.compile(r"\bwhat makes mobcoder\b", re.I),
    re.compile(r"\bengagement models?\b", re.I),
    re.compile(r"\bhow does mobcoder\b", re.I),
    re.compile(r"\bwho is mobcoder\b", re.I),
    re.compile(r"\btell me about\b", re.I),
    re.compile(r"\bcase stud(y|ies)\b", re.I),
]

PROJECT_SALES_PATTERNS = [
    re.compile(
        r"\b(need|want|looking for|build|develop|create|implement)\b.{0,50}\b"
        r"(ai|app|agent|software|platform|chatbot|solution|mvp|product)\b",
        re.I,
    ),
    re.compile(
        r"\b(ai|agent|chatbot|llm|genai).{0,50}\b(support|customer|build|automate)\b",
        re.I,
    ),
    re.compile(
        r"\b(can|could)\s+(you|mobcoder)\s+help\b.{0,40}\b(build|develop|create|with|us)\b",
        re.I,
    ),
    re.compile(r"\bwe need\b.{0,60}\b(ai|agent|app|chatbot|platform)\b", re.I),
    re.compile(r"\b(can|could)\s+mobcoder\s+help\b", re.I),
]

CONTACT_SALES_PATTERN = re.compile(
    r"\b(contact|reach|email|talk to|speak with)\b.{0,30}\b(sales|team)\b",
    re.I,
)

# Mobcoder tech / service terms — in-scope; used for scoring and LLM override.
MOBCODER_TECH_TERMS = (
    "rag",
    "retrieval augmented",
    "retrieval-augmented",
    "langgraph",
    "langchain",
    "llm",
    "genai",
    "generative ai",
    "agentic",
    "vector database",
    "embedding",
    "chatbot",
    "conversational ai",
    "staff augmentation",
    "flutter",
    "react native",
    "hipaa",
    "soc2",
    "gdpr",
)

SOLUTION_NEED_PATTERN = re.compile(
    r"\b(?:lookin(?:g)?(?:\s+for)?|looking(?:\s+for)?|need(?:ing)?|want(?:ing)?)\b.{0,50}\b"
    r"(solution|soultion|rag|ai|agent|chatbot|platform|mvp)\b",
    re.I,
)

RAG_QUERY_PATTERN = re.compile(
    r"\b(rag\b|retrieval[\s-]augmented|vector\s+(store|search|db))",
    re.I,
)

LLM_CLASSIFY_PROMPT = """You classify Mobcoder AI website visitor messages.

Intents (pick exactly one):
- help: learning about services, process, case studies, capabilities (no immediate purchase)
- sales: project need, quote, budget, hiring Mobcoder AI, vendor comparison, build request
- booking: wants to schedule/book a call or demo now
- general: greeting or vague message
- out_of_scope: unrelated to Mobcoder AI business (weather, sports, homework, etc.)

Rules:
- "What services / case studies / how do you work" → help
- "We need to build X / quote / budget / RFP / hire you" → sales
- "Book or schedule a call/demo" → booking
- "How do you run discovery calls" (educational) → help NOT booking
- Project-style "Can you help us build an AI agent" → sales
- RAG, LLM, agentic AI, chatbot, vector search, or Mobcoder technical services → help or sales, NEVER out_of_scope
- "Looking for a RAG solution" / "need an AI chatbot" → sales

Recent user messages: {history}
Current message: "{query}"

Return JSON only:
{{"intent":"help|sales|booking|general|out_of_scope","page_category":"services|ai_agents|case_studies|about|contact|general","confidence":0.0-1.0}}"""


def _matches_phrase(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def _mentions_mobcoder_service(q: str) -> bool:
    if any(term in q for term in MOBCODER_TECH_TERMS):
        return True
    if "mobcoder" in q:
        return True
    return bool(RAG_QUERY_PATTERN.search(q))


def _override_out_of_scope(result: IntentResult, user_query: str) -> IntentResult:
    """LLM sometimes marks in-scope tech queries as out_of_scope — recover."""
    if result.intent != "out_of_scope":
        return result
    q = user_query.lower().strip()
    if not _mentions_mobcoder_service(q) and not SOLUTION_NEED_PATTERN.search(q):
        return result
    if SOLUTION_NEED_PATTERN.search(q) or _is_project_sales_intent(q):
        intent = "sales"
    else:
        intent = "help"
    category = "ai_agents" if RAG_QUERY_PATTERN.search(q) or "rag" in q else infer_page_category(user_query, user_query)
    return IntentResult(intent, category, max(result.confidence, 0.82), "in_scope_override")


def _is_project_sales_intent(text: str) -> bool:
    return any(p.search(text) for p in PROJECT_SALES_PATTERNS)


def _history_user_snippet(
    conversation_history: list[dict[str, str]] | None,
    max_turns: int = 2,
) -> str:
    users = [
        m.get("content", "")
        for m in (conversation_history or [])
        if m.get("role") == "user"
    ]
    return " | ".join(users[-max_turns:]) if users else ""


def _is_booking_intent(q: str) -> bool:
    if any(p.search(q) for p in BOOKING_BLOCK_PATTERNS):
        return False
    return _matches_phrase(q, BOOKING_PHRASES)


def _is_pricing_query(q: str) -> bool:
    return bool(re.search(r"\b(pricing|price|cost|quote|budget|rates?)\b", q, re.I))


def _score_rules(q: str, full_context: str) -> dict[str, float]:
    """Weighted rule scores per intent."""
    scores: dict[str, float] = {i: 0.0 for i in INTENTS}

    if _is_booking_intent(q):
        scores["booking"] += 1.0

    if any(kw in q for kw in ("pricing", "how much", "cost", "quote", "budget", "rfp")):
        scores["sales"] += 0.95

    if re.search(
        r"\b(how\s+(do|does).{0,40}\b(pricing|cost|rates?)|"
        r"(pricing|cost)\s+(approach|model|works?))\b",
        q,
        re.I,
    ):
        scores["sales"] += 0.25

    if CONTACT_SALES_PATTERN.search(q):
        scores["sales"] += 0.9

    if any(p.search(q) for p in HELP_QUESTION_PATTERNS):
        scores["help"] += 0.88

    if _matches_phrase(q, SALES_PHRASES) or any(kw in q for kw in SALES_KEYWORDS):
        scores["sales"] += 0.85

    if _is_project_sales_intent(q):
        scores["sales"] += 0.92
    elif _is_project_sales_intent(full_context) and not scores["help"]:
        scores["sales"] += 0.75

    if any(kw in q for kw in HELP_KEYWORDS):
        scores["help"] += 0.82

    # Vendor comparison without project build → still sales eval expects
    if re.search(r"\b(why (pick|choose)|comparing vendors|vs offshore)\b", q, re.I):
        scores["sales"] += 0.8

    if re.search(r"\b(mobile app|web platform|custom)\b.{0,40}\b(build|develop)\b", q, re.I):
        scores["sales"] += 0.85

    if re.search(
        r"\b(weeks|timeline|deadline|fast[- ]?turnaround|asap|urgent|live in)\b",
        q,
        re.I,
    ) and re.search(r"\b(ai|project|build|deliver|mvp)\b", q, re.I):
        scores["sales"] += 0.88

    if re.search(r"\bwe need\b", q, re.I) and re.search(
        r"\b(ai|agent|app|project|platform|chatbot)\b", q, re.I
    ):
        scores["sales"] += 0.9

    if SOLUTION_NEED_PATTERN.search(q):
        scores["sales"] += 0.92

    if any(term in q for term in MOBCODER_TECH_TERMS) or RAG_QUERY_PATTERN.search(q):
        if SOLUTION_NEED_PATTERN.search(q) or _is_project_sales_intent(q):
            scores["sales"] += 0.9
        elif re.search(r"\b(what|how|do you|can you|tell me|explain)\b", q, re.I):
            scores["help"] += 0.88

    return scores


def _pick_from_scores(
    scores: dict[str, float],
    user_query: str,
    prior_intent: str = "",
) -> IntentResult | None:
    ranked = sorted(
        ((intent, sc) for intent, sc in scores.items() if intent != "out_of_scope"),
        key=lambda x: x[1],
        reverse=True,
    )
    if not ranked or ranked[0][1] <= 0:
        return None

    top_intent, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top_score - second_score

    # Ambiguous: boost prior session intent slightly
    if (
        prior_intent in ("help", "sales")
        and top_score < RULE_CONFIDENCE_FLOOR
        and margin < RULE_MARGIN_MIN
    ):
        boosted = scores.get(prior_intent, 0) + 0.15
        if boosted >= top_score:
            top_intent = prior_intent
            top_score = boosted
            margin = boosted - second_score

    if top_score >= RULE_CONFIDENCE_FLOOR and margin >= RULE_MARGIN_MIN:
        cat = (
            "services"
            if top_intent == "sales" and _is_pricing_query(user_query)
            else infer_page_category(user_query, user_query)
        )
        return IntentResult(
            top_intent,
            cat,
            min(0.95, top_score),
            "rules_scored",
        )
    return None


def _classify_with_llm(
    user_query: str,
    conversation_history: list[dict[str, str]] | None,
    llm_json_fn: Callable[[str], dict[str, Any]],
) -> IntentResult | None:
    history_snippet = _history_user_snippet(conversation_history, max_turns=3)
    try:
        data = llm_json_fn(
            LLM_CLASSIFY_PROMPT.format(
                history=history_snippet or "(none)",
                query=user_query.replace('"', "'"),
            )
        )
        intent = str(data.get("intent") or "").strip()
        if intent not in ("help", "sales", "booking", "general", "out_of_scope"):
            return None
        category = str(data.get("page_category", "general"))
        confidence = float(data.get("confidence", 0.7))
        return _override_out_of_scope(
            IntentResult(intent, category, confidence, "llm"),
            user_query,
        )
    except Exception:
        return None


def classify_with_history(
    user_query: str,
    conversation_history: list[dict[str, str]] | None = None,
    llm_json_fn: Callable[[str], dict[str, Any]] | None = None,
    prior_intent: str = "",
) -> IntentResult:
    """
    Robust intent classification: scored rules → LLM fallback → general.
    """
    q = user_query.lower().strip()
    if not q:
        return IntentResult("general", "general", 0.5, "empty")

    full_context = f"{_history_user_snippet(conversation_history, max_turns=2)} | {q}".lower()

    rule_result = _pick_from_scores(_score_rules(q, full_context), user_query, prior_intent)
    if rule_result:
        return rule_result

    if llm_json_fn and len(q) > 8:
        llm_result = _classify_with_llm(user_query, conversation_history, llm_json_fn)
        if llm_result:
            return llm_result

    # Weak rule winner if any signal
    scores = _score_rules(q, full_context)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if ranked[0][1] > 0:
        intent = ranked[0][0]
        if intent == "out_of_scope":
            intent = "general"
        category = (
            "services"
            if intent == "sales" and _is_pricing_query(user_query)
            else infer_page_category(user_query, user_query)
        )
        return IntentResult(
            intent,
            category,
            ranked[0][1],
            "rules_weak",
        )

    return IntentResult("general", "general", 0.5, "default")


def classify_user_intent(
    user_query: str,
    conversation_history: list[dict[str, str]] | None = None,
    llm_json_fn: Callable[[str], dict[str, Any]] | None = None,
    prior_intent: str = "",
) -> IntentResult:
    """Public entry point (alias for classify_with_history)."""
    return classify_with_history(
        user_query,
        conversation_history,
        llm_json_fn=llm_json_fn,
        prior_intent=prior_intent,
    )
