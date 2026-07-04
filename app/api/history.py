from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_VALID_ROLES = frozenset({"user", "assistant"})


def sanitize_conversation_history(
    history: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """
    Keep only valid OpenAI chat roles. Drops Swagger placeholders (e.g. role='string')
    and empty messages.
    """
    if not history:
        return []

    cleaned: list[dict[str, str]] = []
    for msg in history:
        role = str(msg.get("role", "")).strip().lower()
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        if role not in _VALID_ROLES:
            logger.warning(
                "Skipping invalid conversation_history entry with role=%r",
                msg.get("role"),
            )
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned
