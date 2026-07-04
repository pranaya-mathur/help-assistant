#!/usr/bin/env python3
"""Smoke checks for the website help + lead-capture bot.

  # In-process (TestClient, no running server):
  python scripts/qa_website_bot.py

  # Against live API (pilot or production):
  python scripts/qa_website_bot.py --live --api-url https://devapi-chatbot.mobcoder.ai

Exit 0 when all checks pass.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("APIFY_API_TOKEN", "test")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def _live_get(base: str, path: str) -> tuple[int, dict]:
    import httpx

    url = f"{base.rstrip('/')}{path}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url)
    try:
        body = resp.json()
    except Exception:
        body = {}
    return resp.status_code, body


def _live_post(base: str, path: str, payload: dict) -> tuple[int, dict]:
    import httpx

    url = f"{base.rstrip('/')}{path}"
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, json=payload)
    try:
        body = resp.json()
    except Exception:
        body = {}
    return resp.status_code, body


def smoke_health_live(base: str) -> bool:
    code, body = _live_get(base, "/api/v1/health")
    ok = code == 200 and body.get("status") == "ok"
    count = int(body.get("vector_store_count") or 0)
    return _check("health_ok", ok, f"status={code} chunks={count}") and _check(
        "vector_store_populated", count > 0, f"vector_store_count={count}"
    )


def smoke_help_chat_live(base: str) -> bool:
    payload = {
        "message": "What AI and agentic AI services does Mobcoder AI offer?",
        "page_url": "https://mobcoder.ai/services/agentic-ai",
        "request_id": str(uuid.uuid4()),
    }
    code, body = _live_post(base, "/api/v1/chat", payload)
    response = (body.get("response") or "").strip()
    ok = code == 200 and len(response) > 40
    intent_ok = body.get("intent") in {"help", "sales", "general", "booking"}
    session_ok = bool(body.get("session_id"))
    chips = body.get("suggested_replies") or []
    chips_ok = 0 < len(chips) <= 4
    return (
        _check("chat_help_200", code == 200, f"status={code}")
        and _check("chat_help_response", ok, f"len={len(response)}")
        and _check("chat_intent", intent_ok, f"intent={body.get('intent')}")
        and _check("chat_session_id", session_ok, f"session={body.get('session_id', '')[:8]}")
        and _check("suggested_replies_cap", chips_ok, f"chips={len(chips)}")
    )


def smoke_lead_fields_live(base: str) -> bool:
    session_id = str(uuid.uuid4())
    payload = {
        "message": "My name is Alex Rivera from Acme Corp, email alex@acme.com. We need an AI chatbot.",
        "session_id": session_id,
        "page_url": "https://mobcoder.ai/contact-us",
        "lead_consent": True,
        "lead_profile": {
            "name": "Alex Rivera",
            "email": "alex@acme.com",
            "company": "Acme Corp",
        },
        "request_id": str(uuid.uuid4()),
    }
    code, body = _live_post(base, "/api/v1/chat", payload)
    profile = body.get("lead_profile") or {}
    ok = code == 200 and profile.get("email") == "alex@acme.com"
    return _check("lead_capture_email", ok, f"email={profile.get('email', '')}")


def smoke_widget_context_live(base: str) -> bool:
    code, body = _live_get(
        base,
        "/api/v1/widget-context?page_url=https://mobcoder.ai/pricing",
    )
    ok = code == 200 and body.get("page_category") == "pricing" and body.get("starter_chips")
    return _check("widget_context", ok, f"status={code} chips={len(body.get('starter_chips') or [])}")


def smoke_page_context_chat_live(base: str) -> bool:
    payload = {
        "message": "hi",
        "page_url": "https://mobcoder.ai/case-studies",
        "page_title": "Mobcoder AI Case Studies",
        "request_id": str(uuid.uuid4()),
    }
    code, body = _live_post(base, "/api/v1/chat", payload)
    response = (body.get("response") or "").strip()
    citations = body.get("citations") or []
    ok = code == 200 and len(response) > 20
    cite_ok = not citations or any(
        "case-stud" in str(c.get("source_url") or "").lower() for c in citations
    )
    return (
        _check("page_context_chat_200", code == 200, f"status={code}")
        and _check("page_context_chat_response", ok, f"len={len(response)}")
        and _check("page_context_citations", cite_ok or not citations, f"citations={len(citations)}")
    )


def smoke_case_study_citation_live(base: str) -> bool:
    payload = {
        "message": "Tell me about a fintech case study",
        "page_url": "https://mobcoder.ai/case-studies",
        "request_id": str(uuid.uuid4()),
    }
    code, body = _live_post(base, "/api/v1/chat", payload)
    citations = body.get("citations") or []
    if not citations:
        return _check("case_study_citation_snippet", True, "no citations (skipped)")
    snippet_ok = any((c.get("snippet") or "").strip() for c in citations)
    return _check(
        "case_study_citation_snippet",
        code == 200 and snippet_ok,
        f"status={code} citations={len(citations)}",
    )


def smoke_suggested_replies_unit() -> bool:
    from app.agent.suggested_replies import MAX_SUGGESTIONS, build_suggested_replies

    chips = build_suggested_replies(
        intent="help",
        stage="discover",
        page_category="services",
        profile_question="",
        needs_contact_info=False,
        lead_profile={},
    )
    ok = 0 < len(chips) <= MAX_SUGGESTIONS
    return _check("unit_suggested_replies", ok, f"chips={len(chips)} max={MAX_SUGGESTIONS}")


def smoke_inprocess() -> bool:
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    health = client.get("/api/v1/health")
    health_ok = health.status_code == 200
    body = health.json()
    count = int(body.get("vector_store_count") or 0)
    status_ok = body.get("status") == "ok"

    ctx = client.get(
        "/api/v1/widget-context",
        params={"page_url": "https://mobcoder.ai/pricing"},
    )
    ctx_ok = ctx.status_code == 200 and ctx.json().get("page_category") == "pricing"

    return (
        _check("inprocess_health", health_ok)
        and _check("inprocess_status_ok", status_ok, f"status={body.get('status')}")
        and _check("inprocess_vector_store", count > 0, f"count={count}")
        and _check("inprocess_widget_context", ctx_ok, f"category={ctx.json().get('page_category')}")
        and smoke_suggested_replies_unit()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Website bot QA smoke checks")
    parser.add_argument("--live", action="store_true", help="Hit a running API over HTTP")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("WEBSITE_BOT_API_URL", "http://127.0.0.1:8001"),
        help="API base URL (no trailing path)",
    )
    args = parser.parse_args()

    print(f"=== Website bot QA ({'live: ' + args.api_url if args.live else 'in-process'}) ===")

    if args.live:
        results = [
            smoke_health_live(args.api_url),
            smoke_widget_context_live(args.api_url),
            smoke_help_chat_live(args.api_url),
            smoke_page_context_chat_live(args.api_url),
            smoke_case_study_citation_live(args.api_url),
            smoke_lead_fields_live(args.api_url),
        ]
    else:
        results = [smoke_inprocess()]

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"=== {passed}/{total} check groups passed ===")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
