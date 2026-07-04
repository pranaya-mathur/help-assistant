"""Offline smoke tests for eval harness (no live OpenAI)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agent.graph import run_agent


def _mock_run_agent(**kwargs):
    q = kwargs.get("user_query", "")
    intent = "sales" if "quote" in q.lower() or "build an AI agent" in q else "help"
    if "schedule" in q.lower() or "book a demo" in q.lower():
        intent = "booking"
    return {
        "final_response": f"MobCoder can help with that. See https://mobcoder.ai/services for details.",
        "intent": intent,
        "stage": "educate",
        "citations": [
            {
                "page_title": "Services",
                "source_url": "https://mobcoder.ai/services",
                "citation_text": "MobCoder services",
                "score": 0.9,
            }
        ],
        "grounding_passed": True,
        "lead_profile": kwargs.get("lead_profile") or {},
    }


def test_golden_dataset_loads():
    path = Path("data/evals/golden_questions.json")
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert len(cases) >= 25


def test_eval_case_mock_passes_intent():
    with patch("run_eval.run_agent", side_effect=_mock_run_agent):
        from run_eval import _run_case

        ok, reasons, state = _run_case(
            {
                "question": "We need to build an AI agent for customer support. Can you help?",
                "must_contain_any": ["MobCoder"],
                "expect_intent": "sales",
            }
        )
        assert ok, reasons
        assert state["intent"] == "sales"


def test_eval_min_pass_rate_allows_partial_pass(tmp_path):
    dataset = tmp_path / "eval.json"
    dataset.write_text(json.dumps([{"id": "a"}, {"id": "b"}]), encoding="utf-8")

    class _Settings:
        eval_dataset_path = str(dataset)

    outcomes = [
        (True, [], {"intent": "help", "stage": "educate", "final_response": "ok", "citations": []}),
        (False, ["missing"], {"intent": "help", "stage": "educate", "final_response": "bad", "citations": []}),
    ]

    with (
        patch("run_eval.get_settings", return_value=_Settings()),
        patch("run_eval._run_case", side_effect=outcomes),
        patch.object(sys, "argv", ["run_eval.py", "--min-pass-rate", "0.50", "--skip-preflight"]),
        pytest.raises(SystemExit) as exc,
    ):
        from run_eval import main

        main()

    assert exc.value.code == 0


def test_eval_require_perfect_fails_on_any_failure(tmp_path):
    dataset = tmp_path / "eval.json"
    dataset.write_text(json.dumps([{"id": "a"}, {"id": "b"}]), encoding="utf-8")

    class _Settings:
        eval_dataset_path = str(dataset)

    outcomes = [
        (True, [], {"intent": "help", "stage": "educate", "final_response": "ok", "citations": []}),
        (False, ["missing"], {"intent": "help", "stage": "educate", "final_response": "bad", "citations": []}),
    ]

    with (
        patch("run_eval.get_settings", return_value=_Settings()),
        patch("run_eval._run_case", side_effect=outcomes),
        patch.object(sys, "argv", ["run_eval.py", "--min-pass-rate", "0.50", "--require-perfect", "--skip-preflight"]),
        pytest.raises(SystemExit) as exc,
    ):
        from run_eval import main

        main()

    assert exc.value.code == 1


def test_eval_preflight_fails_when_openai_key_missing(tmp_path):
    from run_eval import EvalPreflightError, _preflight_eval

    dataset = tmp_path / "eval.json"
    dataset.write_text("[]", encoding="utf-8")

    class _Settings:
        openai_api_key = ""
        eval_dataset_path = str(dataset)

    with (
        patch("run_eval.get_settings", return_value=_Settings()),
        patch("run_eval.get_vector_store") as mock_store,
        pytest.raises(EvalPreflightError) as exc,
    ):
        mock_store.return_value.count.return_value = 10
        _preflight_eval()

    assert "OPENAI_API_KEY is not set" in str(exc.value)
    assert "setup/infrastructure failure" in str(exc.value)


def test_eval_preflight_fails_when_vector_store_empty(tmp_path):
    from run_eval import EvalPreflightError, _preflight_eval

    dataset = tmp_path / "eval.json"
    dataset.write_text("[]", encoding="utf-8")

    class _Settings:
        openai_api_key = "sk-test"
        eval_dataset_path = str(dataset)

    with (
        patch("run_eval.get_settings", return_value=_Settings()),
        patch("run_eval.get_vector_store") as mock_store,
        pytest.raises(EvalPreflightError) as exc,
    ):
        mock_store.return_value.count.return_value = 0
        _preflight_eval()

    assert "Vector store has 0 documents/chunks" in str(exc.value)


def test_eval_preflight_fails_when_openai_connectivity_fails(tmp_path):
    from run_eval import EvalPreflightError, _preflight_eval

    dataset = tmp_path / "eval.json"
    dataset.write_text("[]", encoding="utf-8")

    class _Settings:
        openai_api_key = "sk-test"
        eval_dataset_path = str(dataset)

    with (
        patch("run_eval.get_settings", return_value=_Settings()),
        patch("run_eval.get_vector_store") as mock_store,
        patch("run_eval.get_embedding_client") as mock_embedder,
        pytest.raises(EvalPreflightError) as exc,
    ):
        mock_store.return_value.count.return_value = 10
        mock_embedder.return_value.embed_text.side_effect = RuntimeError("network down")
        _preflight_eval()

    assert "OpenAI embeddings connectivity failed" in str(exc.value)
    assert "setup/infrastructure failure" in str(exc.value)


def test_eval_wrapper_exists():
    assert Path("scripts/run_eval.py").exists()
