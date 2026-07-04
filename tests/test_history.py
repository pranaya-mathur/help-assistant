from app.api.history import sanitize_conversation_history


def test_drops_swagger_placeholder_role():
    raw = [{"role": "string", "content": "What AI services do you offer?"}]
    assert sanitize_conversation_history(raw) == []


def test_keeps_valid_roles():
    raw = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello from MobCoder."},
    ]
    assert len(sanitize_conversation_history(raw)) == 2
