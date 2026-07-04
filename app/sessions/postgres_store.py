from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.infra.postgres import get_pool, prune_expired_sessions
from app.sessions.store import SessionData, SessionStoreBackend

logger = logging.getLogger(__name__)
_MAX_HISTORY_TURNS = 20
_SESSION_TTL_DAYS = 30


class PostgresSessionStore(SessionStoreBackend):
    def __init__(self) -> None:
        self._write_lock = threading.Lock()
        deleted = prune_expired_sessions(_SESSION_TTL_DAYS)
        if deleted:
            logger.info(
                "Pruned %d expired sessions from Postgres (TTL %d days)",
                deleted,
                _SESSION_TTL_DAYS,
            )

    @staticmethod
    def _row_to_session(row: dict[str, Any]) -> SessionData:
        lead_profile = row.get("lead_profile") or {}
        conversation_history = row.get("conversation_history") or []
        metadata = row.get("metadata") or {}
        if not isinstance(lead_profile, dict):
            lead_profile = {}
        if not isinstance(conversation_history, list):
            conversation_history = []
        if not isinstance(metadata, dict):
            metadata = {}
        updated_at = row.get("updated_at")
        if hasattr(updated_at, "isoformat"):
            updated_at = updated_at.isoformat()
        return SessionData(
            session_id=str(row["session_id"]),
            lead_profile=lead_profile,
            conversation_history=conversation_history,
            intent=str(row.get("intent") or ""),
            stage=str(row.get("stage") or "discover"),
            updated_at=str(updated_at or ""),
            metadata=metadata,
        )

    def create_session(self) -> str:
        return self._create_session_with_id(str(uuid.uuid4()))

    def _create_session_with_id(self, session_id: str) -> str:
        now = datetime.now(timezone.utc)
        with self._write_lock:
            pool = get_pool()
            with pool.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO chat_sessions (session_id, updated_at)
                    VALUES (%s, %s)
                    ON CONFLICT (session_id) DO NOTHING
                    """,
                    (UUID(session_id), now),
                )
        return session_id

    def get(self, session_id: str) -> SessionData | None:
        try:
            session_uuid = UUID(session_id)
        except ValueError:
            return None

        pool = get_pool()
        with pool.connection() as conn:
            row = conn.execute(
                """
                SELECT session_id, lead_profile, conversation_history,
                       intent, stage, updated_at, metadata
                FROM chat_sessions
                WHERE session_id = %s
                """,
                (session_uuid,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_session(row)

    def get_or_create(self, session_id: str | None) -> SessionData:
        if session_id:
            existing = self.get(session_id)
            if existing:
                return existing
            try:
                UUID(session_id)
            except ValueError:
                pass
            else:
                # Client (widget) already has this ID in localStorage — use it so
                # the session it creates matches what the client will send on the
                # next turn, instead of silently minting an unrelated new UUID
                # (which used to fragment history/analytics across two IDs).
                sid = self._create_session_with_id(session_id)
                data = self.get(sid)
                assert data is not None
                return data
        sid = self.create_session()
        data = self.get(sid)
        assert data is not None
        return data

    def save_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        lead_profile: dict[str, Any],
        intent: str = "",
        stage: str = "discover",
        request_id: str = "",
    ) -> None:
        try:
            session_uuid = UUID(session_id)
        except ValueError:
            return

        now = datetime.now(timezone.utc)

        with self._write_lock:
            pool = get_pool()
            with pool.connection() as conn:
                with conn.transaction():
                    # Lock the row for the rest of this transaction so a concurrent
                    # save_turn on the same session_id (double-send, retried request,
                    # two browser tabs) blocks here instead of reading the same stale
                    # history and overwriting this turn on write (lost-update race).
                    row = conn.execute(
                        """
                        SELECT conversation_history FROM chat_sessions
                        WHERE session_id = %s
                        FOR UPDATE
                        """,
                        (session_uuid,),
                    ).fetchone()
                    existing_history = (row["conversation_history"] if row else None) or []
                    if not isinstance(existing_history, list):
                        existing_history = []

                    history = list(existing_history)
                    history.append({"role": "user", "content": user_message})
                    # request_id ties this exact turn to the id the client uses for
                    # feedback (POST /feedback message_id) — without it, feedback
                    # enrichment can only guess which turn was rated.
                    assistant_turn: dict[str, Any] = {"role": "assistant", "content": assistant_message}
                    if request_id:
                        assistant_turn["request_id"] = request_id
                    history.append(assistant_turn)
                    history = history[-_MAX_HISTORY_TURNS * 2 :]
                    user_turn_index = len(history) - 2
                    assistant_turn_index = len(history) - 1

                    conn.execute(
                        """
                        UPDATE chat_sessions
                        SET lead_profile = %s::jsonb,
                            conversation_history = %s::jsonb,
                            intent = %s,
                            stage = %s,
                            updated_at = %s
                        WHERE session_id = %s
                        """,
                        (
                            json.dumps(lead_profile),
                            json.dumps(history),
                            intent,
                            stage,
                            now,
                            session_uuid,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO conversation_turns (
                            session_id, turn_index, role, content, intent, stage
                        )
                        VALUES (%s, %s, 'user', %s, %s, %s)
                        ON CONFLICT (session_id, turn_index) DO UPDATE SET
                            content = EXCLUDED.content,
                            intent = EXCLUDED.intent,
                            stage = EXCLUDED.stage,
                            created_at = now()
                        """,
                        (session_uuid, user_turn_index, user_message, intent, stage),
                    )
                    conn.execute(
                        """
                        INSERT INTO conversation_turns (
                            session_id, turn_index, role, content, intent, stage
                        )
                        VALUES (%s, %s, 'assistant', %s, %s, %s)
                        ON CONFLICT (session_id, turn_index) DO UPDATE SET
                            content = EXCLUDED.content,
                            intent = EXCLUDED.intent,
                            stage = EXCLUDED.stage,
                            created_at = now()
                        """,
                        (session_uuid, assistant_turn_index, assistant_message, intent, stage),
                    )

    def update_metadata(
        self,
        session_id: str,
        *,
        page_url: str = "",
        referrer: str = "",
    ) -> None:
        data = self.get(session_id)
        if not data:
            return
        meta = dict(data.metadata)
        if page_url:
            meta.setdefault("first_page_url", page_url)
            meta["page_url"] = page_url
            meta["last_page_url"] = page_url
        if referrer:
            meta["referrer"] = referrer
        self._persist_metadata(session_id, meta)

    def patch_metadata(self, session_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        data = self.get(session_id)
        if not data:
            return
        meta = dict(data.metadata)
        meta.update(updates)
        self._persist_metadata(session_id, meta)

    def _persist_metadata(self, session_id: str, meta: dict[str, Any]) -> None:
        try:
            session_uuid = UUID(session_id)
        except ValueError:
            return

        now = datetime.now(timezone.utc)
        with self._write_lock:
            pool = get_pool()
            with pool.connection() as conn:
                conn.execute(
                    """
                    UPDATE chat_sessions
                    SET metadata = %s::jsonb, updated_at = %s
                    WHERE session_id = %s
                    """,
                    (json.dumps(meta), now, session_uuid),
                )

    def save_feedback(
        self,
        session_id: str,
        message_id: str,
        rating: int,
        comment: str = "",
    ) -> None:
        if rating not in (1, -1):
            raise ValueError("rating must be 1 (positive) or -1 (negative)")

        try:
            session_uuid = UUID(session_id)
        except ValueError:
            raise ValueError("invalid session_id")

        with self._write_lock:
            pool = get_pool()
            with pool.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO feedback (session_id, message_id, rating, comment, created_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (session_id, message_id) DO UPDATE SET
                        rating = EXCLUDED.rating,
                        comment = EXCLUDED.comment,
                        created_at = EXCLUDED.created_at
                    """,
                    (session_uuid, message_id, rating, comment or ""),
                )

    def get_feedback_stats(self) -> dict[str, Any]:
        pool = get_pool()
        with pool.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) AS positive,
                    SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) AS negative
                FROM feedback
                """
            ).fetchone()
        if not row:
            return {"total": 0, "positive": 0, "negative": 0, "score": 0.0}
        total = int(row["total"] or 0)
        positive = int(row["positive"] or 0)
        negative = int(row["negative"] or 0)
        score = round(positive / total, 4) if total else 0.0
        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "score": score,
        }

    def list_feedback(
        self,
        limit: int = 50,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = min(max(1, limit), 500)
        pool = get_pool()
        with pool.connection() as conn:
            if session_id:
                try:
                    session_uuid = UUID(session_id)
                except ValueError:
                    return []
                rows = conn.execute(
                    """
                    SELECT session_id, message_id, rating, comment, created_at
                    FROM feedback
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (session_uuid, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT session_id, message_id, rating, comment, created_at
                    FROM feedback
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            created_at = row["created_at"]
            if hasattr(created_at, "isoformat"):
                created_at = created_at.isoformat()
            out.append(
                {
                    "session_id": str(row["session_id"]),
                    "message_id": row["message_id"],
                    "rating": row["rating"],
                    "comment": row["comment"] or "",
                    "created_at": str(created_at),
                }
            )
        return out

    def list_leads(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent sessions with a captured name or email, newest first."""
        limit = min(max(1, limit), 500)
        pool = get_pool()
        with pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT session_id, lead_profile, intent, stage, updated_at
                FROM chat_sessions
                WHERE COALESCE(lead_profile->>'name', '') <> ''
                   OR COALESCE(lead_profile->>'email', '') <> ''
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            updated_at = row["updated_at"]
            if hasattr(updated_at, "isoformat"):
                updated_at = updated_at.isoformat()
            profile = row.get("lead_profile") or {}
            if not isinstance(profile, dict):
                profile = {}
            out.append(
                {
                    "session_id": str(row["session_id"]),
                    "lead_profile": profile,
                    "intent": str(row.get("intent") or ""),
                    "stage": str(row.get("stage") or "discover"),
                    "updated_at": str(updated_at or ""),
                }
            )
        return out
