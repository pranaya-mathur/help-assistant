from app.crawler.chunker import chunk_page
from app.crawler.schema import ScrapedPage


def test_chunk_page():
    paragraph = (
        "MobCoder offers custom software development, mobile and web applications, "
        "cloud engineering, and agentic AI systems for enterprises and startups. "
    ) * 8
    page = ScrapedPage(
        source_url="https://mobcoder.ai/services",
        page_title="Services",
        page_category="services",
        clean_text=paragraph,
    )
    chunks = chunk_page(page)
    assert len(chunks) >= 1
    assert chunks[0].page_category == "services"
    assert chunks[0].citation_text
