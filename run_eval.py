#!/usr/bin/env python3
"""Run golden-set evaluation against the MobCoder agent."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

from app.agent.graph import run_agent
from app.config.settings import get_settings
from app.rag.embeddings import get_embedding_client
from app.rag.vector_store import get_vector_store

MOBCODER_DOMAINS = ("mobcoder.ai", "www.mobcoder.ai")
DEFAULT_PASS_RATE = 0.90


class EvalPreflightError(RuntimeError):
    pass


def _citations_use_mobcoder(citations: list) -> bool:
    for c in citations:
        url = c.get("source_url", "") if isinstance(c, dict) else getattr(c, "source_url", "")
        try:
            host = urlparse(url).netloc.lower()
            if any(host == d or host.endswith("." + d) for d in MOBCODER_DOMAINS):
                return True
        except Exception:
            continue
    return False


def _response_has_mobcoder_link(response: str) -> bool:
    return "mobcoder.ai" in response.lower()


def _run_case(case: dict) -> tuple[bool, list[str], dict]:
    history: list[dict[str, str]] = []
    profile: dict = {}

    if case.get("multi_turn") and case.get("messages"):
        messages = case["messages"]
        state = {}
        for i, msg in enumerate(messages):
            state = run_agent(
                user_query=msg,
                conversation_history=history,
                lead_profile=profile,
            )
            history.append({"role": "user", "content": msg})
            history.append({"role": "assistant", "content": state.get("final_response", "")})
            profile = state.get("lead_profile") or profile
    else:
        state = run_agent(
            user_query=case["question"],
            conversation_history=[],
            lead_profile={},
        )

    response = state.get("final_response", "")
    citations = state.get("citations", [])
    reasons: list[str] = []
    checks_ok = True

    must_contain_any = case.get("must_contain_any", [])
    if must_contain_any and not any(p.lower() in response.lower() for p in must_contain_any):
        checks_ok = False
        reasons.append(f"missing any of: {', '.join(must_contain_any)}")

    for phrase in case.get("must_not_contain", []):
        if phrase.lower() in response.lower():
            checks_ok = False
            reasons.append(f"forbidden: {phrase}")

    if case.get("require_citations") and not citations:
        checks_ok = False
        reasons.append("no citations")

    require_mob = case.get("require_mobcoder_citations", False)
    if require_mob:
        has_chunk_cite = _citations_use_mobcoder(citations)
        has_link = _response_has_mobcoder_link(response)
        if not has_chunk_cite and not has_link:
            checks_ok = False
            reasons.append("no mobcoder.ai citation/link")

    if case.get("require_grounding") and not state.get("grounding_passed", True):
        checks_ok = False
        reasons.append("grounding failed")

    if case.get("must_answer_before_qualify"):
        if len(response) < 120 and "?" in response and "mobcoder" not in response.lower():
            checks_ok = False
            reasons.append("answer too short / qualify-only")

    expect_intent = case.get("expect_intent")
    if expect_intent:
        actual_intent = state.get("intent")
        intent_ok = actual_intent == expect_intent
        # Phase 1 pilot (OPERATING_MODE=help) intentionally reports every non-out-of-scope
        # message as intent="help" — there is no sales/booking qualify layer to route to.
        # A golden case written for sales/booking labeling is satisfied by "help" here.
        if (
            not intent_ok
            and get_settings().is_help_mode()
            and expect_intent in ("sales", "booking")
            and actual_intent == "help"
        ):
            intent_ok = True
        if not intent_ok:
            checks_ok = False
            reasons.append(f"intent={actual_intent} want {expect_intent}")

    if case.get("expect_stage") and state.get("stage") != case["expect_stage"]:
        checks_ok = False
        reasons.append(f"stage={state.get('stage')} want {case['expect_stage']}")

    return checks_ok, reasons, state


def _preflight_eval(*, allow_empty_kb: bool = False) -> None:
    settings = get_settings()
    problems: list[str] = []

    if not settings.openai_api_key:
        problems.append("- OPENAI_API_KEY is not set.")

    path = Path(settings.eval_dataset_path)
    if not path.exists():
        problems.append(f"- Eval dataset not found: {path}")

    try:
        count = get_vector_store().count()
    except Exception as exc:
        problems.append(f"- Vector store is unavailable: {exc}")
    else:
        if count <= 0 and not allow_empty_kb:
            problems.append("- Vector store has 0 documents/chunks.")

    if not problems:
        try:
            get_embedding_client().embed_text("MobCoder eval preflight")
        except Exception as exc:
            problems.append(f"- OpenAI embeddings connectivity failed: {exc}")

    if problems:
        message = [
            "Eval preflight failed. This is a setup/infrastructure failure, not a model-quality result.",
            "",
            *problems,
            "",
            "Fix steps:",
            "1. Export OPENAI_API_KEY with a valid key.",
            "2. Run crawler/ingestion: python3 scripts/crawl_mobcoder.py && python3 scripts/build_knowledge_md.py && python3 scripts/ingest.py",
            "3. Verify vector count: python3 -c \"from app.rag.vector_store import get_vector_store; print(get_vector_store().count())\"",
            "4. Re-run: python3 scripts/run_eval.py --min-pass-rate 0.90",
            "",
            "For local smoke tests only, use --allow-empty-kb when intentionally testing without an indexed KB.",
        ]
        raise EvalPreflightError("\n".join(message))


def main() -> None:
    parser = argparse.ArgumentParser(description="MobCoder agent golden eval")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=float(os.environ.get("EVAL_MIN_PASS_RATE", DEFAULT_PASS_RATE)),
        help="Minimum pass rate (0-1) to exit 0",
    )
    parser.add_argument("--id", type=str, help="Run single case by id")
    parser.add_argument(
        "--require-perfect",
        action="store_true",
        help="Exit 1 if any case fails, even when --min-pass-rate is met.",
    )
    parser.add_argument(
        "--allow-empty-kb",
        action="store_true",
        help="Allow eval to run with an empty vector store (local smoke only).",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip eval setup checks (tests only; not for release gates).",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not args.skip_preflight:
        try:
            _preflight_eval(allow_empty_kb=args.allow_empty_kb)
        except EvalPreflightError as exc:
            print(str(exc))
            sys.exit(2)

    path = Path(settings.eval_dataset_path)
    if not path.exists():
        print(f"Eval file not found: {path}")
        sys.exit(1)

    cases = json.loads(path.read_text(encoding="utf-8"))
    if args.id:
        cases = [c for c in cases if c.get("id") == args.id]
        if not cases:
            print(f"No case with id={args.id}")
            sys.exit(1)

    passed = 0
    failed = 0

    print(f"\nRunning {len(cases)} eval cases (min pass rate {args.min_pass_rate:.0%})…\n")

    for i, case in enumerate(cases, 1):
        label = case.get("id", case.get("question", "")[:40])
        checks_ok, reasons, state = _run_case(case)
        status = "PASS" if checks_ok else "FAIL"
        if checks_ok:
            passed += 1
        else:
            failed += 1

        print(f"[{i}] {status} — {label}")
        if reasons:
            print(f"     {', '.join(reasons)}")
        print(
            f"     intent={state.get('intent')} stage={state.get('stage')} "
            f"len={len(state.get('final_response', ''))} "
            f"citations={len(state.get('citations', []))}"
        )

    total = passed + failed
    pct = (passed / total) if total else 0.0
    print("\nSummary")
    print(f"  total cases: {total}")
    print(f"  passed cases: {passed}")
    print(f"  failed cases: {failed}")
    print(f"  pass rate: {pct:.2%}")
    print(f"  threshold: {args.min_pass_rate:.2%}")

    if pct < args.min_pass_rate:
        print(f"  final status: FAIL (pass rate below threshold)")
        sys.exit(1)
    if args.require_perfect and failed:
        print("  final status: FAIL (--require-perfect set and at least one case failed)")
        sys.exit(1)
    print("  final status: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
