from app.agent.claim_validator import GroundingResult
from app.agent.grounding import _llm_claim_check


def test_llm_grounding_context_includes_deep_chunk_text():
    answer = "MobCoder uses project-based pricing."
    deep_text = "x" * 1200 + " project-based pricing "
    seen: dict[str, str] = {}

    def fake_llm_json(prompt: str):
        seen["prompt"] = prompt
        return {"unsupported_claims": []}

    fixed, result = _llm_claim_check(
        answer,
        [{"page_title": "Pricing", "chunk_text": deep_text}],
        fake_llm_json,
        GroundingResult(True, False, []),
    )

    assert fixed == answer
    assert result.is_grounded
    assert "project-based pricing" in seen["prompt"]


def test_llm_grounding_ignores_high_overlap_false_positive():
    answer = "MobCoder offers flexible engagement models, including dedicated teams, staff augmentation, and project-based pricing."
    context = "MobCoder offers flexible engagement models: dedicated teams, staff augmentation, and project-based pricing."

    def fake_llm_json(prompt: str):
        return {"unsupported_claims": [answer]}

    fixed, result = _llm_claim_check(
        answer,
        [{"page_title": "Pricing", "chunk_text": context}],
        fake_llm_json,
        GroundingResult(True, False, []),
    )

    assert fixed == answer
    assert result.is_grounded
