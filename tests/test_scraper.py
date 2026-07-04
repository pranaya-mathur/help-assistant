from app.crawler.page_loader import (
    apify_item_to_page_dict,
    infer_page_category,
    infer_source_tier,
    is_geo_seo_url,
)
from app.crawler.schema import url_is_official


def test_url_is_official():
    assert url_is_official("https://mobcoder.ai/services")
    assert url_is_official("https://www.mobcoder.ai/about")
    assert not url_is_official("https://example.com/")


def test_infer_page_category():
    assert infer_page_category("https://mobcoder.ai/case-studies/foo", "") == "case_studies"
    assert infer_page_category("https://mobcoder.ai/ai-agents", "") == "ai_agents"
    assert (
        infer_page_category("https://mobcoder.ai/blog/agentic-ai-frameworks", "")
        == "blog"
    )


def test_geo_seo_not_ai_agents():
    url = "https://mobcoder.ai/best-ai-development-company-in-denver"
    assert is_geo_seo_url(url)
    assert infer_page_category(url, "") == "general"
    assert infer_source_tier(url, "general") == "seo_geo"


def test_core_ai_page():
    url = "https://mobcoder.ai/ai-agents"
    assert infer_source_tier(url, "ai_agents") == "core"
    assert infer_page_category(url, "") == "ai_agents"


def test_apify_item_to_page_dict():
    item = {
        "url": "https://mobcoder.ai/services",
        "title": "Services",
        "markdown": "MobCoder builds custom software and AI solutions.",
    }
    d = apify_item_to_page_dict(item)
    assert d is not None
    assert d["source_url"] == "https://mobcoder.ai/services"
    assert d["source_tier"] == "core"
    assert "MobCoder" in d["clean_text"]
