from unittest.mock import patch

from fastapi.testclient import TestClient

from main import create_app


@patch("app.observability.events.emit_event")
def test_events_widget_opened(mock_emit):
    client = TestClient(create_app())
    res = client.post(
        "/api/v1/events",
        json={"event": "widget_opened", "page_url": "https://mobcoder.ai/ai"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["session_id"]
    mock_emit.assert_called_once()
    assert mock_emit.call_args[0][0] == "widget_opened"


@patch("app.observability.events.emit_event")
def test_events_cta_clicked(mock_emit):
    client = TestClient(create_app())
    res = client.post(
        "/api/v1/events",
        json={
            "event": "cta_clicked",
            "cta": "calendly_footer",
            "session_id": "test-session-123",
        },
    )
    assert res.status_code == 200
    mock_emit.assert_called_once()
    body = mock_emit.call_args[0][1]
    assert body["cta"] == "calendly_footer"
