from fastapi.testclient import TestClient

from main import app


def test_widget_context_pricing():
    client = TestClient(app)
    res = client.get(
        "/api/v1/widget-context",
        params={"page_url": "https://mobcoder.ai/pricing"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["page_category"] == "pricing"
    assert data["opener"]
    assert len(data["starter_chips"]) >= 1
    assert any("pricing" in c.lower() or "quote" in c.lower() for c in data["starter_chips"])


def test_widget_context_requires_page_url():
    client = TestClient(app)
    res = client.get("/api/v1/widget-context")
    assert res.status_code == 422
