"""Rule-only intent cases from golden eval (no live LLM)."""
from __future__ import annotations

import json
from pathlib import Path

from app.agent.intent_classifier import classify_with_history

GOLDEN = Path("data/evals/golden_questions.json")


def _cases_with_expected_intent():
    for case in json.loads(GOLDEN.read_text(encoding="utf-8")):
        if case.get("expect_intent"):
            yield case


def test_golden_intents_rule_only():
    """All expect_intent cases should match without LLM (rules_scored or rules_weak)."""
    failures = []
    for case in _cases_with_expected_intent():
        if case.get("multi_turn"):
            continue
        q = case["question"]
        r = classify_with_history(q, llm_json_fn=None)
        if r.intent != case["expect_intent"]:
            failures.append(
                f"{case['id']}: got {r.intent} want {case['expect_intent']} ({r.source})"
            )
    assert not failures, "\n".join(failures)
