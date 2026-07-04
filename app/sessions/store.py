from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_MAX_HISTORY_TURNS = 20
_SESSION_TTL_DAYS = 30
_SESSION_TTL_SECONDS = _SESSION_TTL_DAYS * 24 * 3600
_REDIS_KEY_PREFIX = "mobcoder:session:"

# Thread-local connection pool for SQLite
_tl = threading.local()


@dataclass
class SessionData:
    session_id: str
    lead_profile: dict[str, Any]
    conversation_history: list[dict[str, str]]
    intent: str
    stage: str
    updated_at: str
    metadata: dict[str, Any]


class SessionStoreBackend(ABC):
    @abstractmethod
    def create_session(self) -> str:
        ...

    @abstractmethod
    def get(self, session_id: str) -> SessionData | None:
        ...

    @abstractmethod
    def get_or_create(self, session_id: str | None) -> SessionData:
        ...

    @abstractmethod
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
        ...

    @abstractmethod
    def update_metadata(
        self,
        session_id: str,
        *,
        page_url: str = "",
        referrer: str = "",
    ) -> None:
        ...

    @abstractmethod
    def patch_metadata(self, session_id: str, updates: dict[str, Any]) -> None:
        ...

    def save_feedback(
        self,
        session_id: str,
        message_id: str,
        rating: int,
        comment: str = "",
    ) -> None:
        """Persist thumbs-up / thumbs-down feedback. Default no-op for backends that don't support it."""

    def get_feedback_stats(self) -> dict[str, Any]:
        """Return aggregate feedback stats. Default returns empty dict."""
        return {}

    def list_feedback(
        self,
        limit: int = 50,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent feedback records. Default no-op for backends that don't support it."""
        return []

    def list_leads(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent sessions that have at least a name or email captured.

        Default no-op for backends that don't support it.
        """
        return []


class SQLiteSessionStore(SessionStoreBackend):
    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_db()
        self._prune_expired_sessions()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(_tl, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-16000")
            conn.execute("PRAGMA mmap_size=67108864")
            conn.execute("PRAGMA foreign_keys=ON")
            _tl.conn = conn
        return conn

    def _init_db(self) -> None:
        with self._write_lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        session_id   TEXT PRIMARY KEY,
                        lead_profile TEXT NOT NULL DEFAULT '{}',
                        conversation_history TEXT NOT NULL DEFAULT '[]',
                        intent       TEXT NOT NULL DEFAULT '',
                        stage        TEXT NOT NULL DEFAULT 'discover',
                        metadata     TEXT NOT NULL DEFAULT '{}',
                        updated_at   TEXT NOT NULL
                    )
                    """
                )
                existing_cols = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()
                }
                migrations = [
                    ("intent", "ALTER TABLE chat_sessions ADD COLUMN intent TEXT NOT NULL DEFAULT ''"),
                    ("stage", "ALTER TABLE chat_sessions ADD COLUMN stage TEXT NOT NULL DEFAULT 'discover'"),
                    ("metadata", "ALTER TABLE chat_sessions ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"),
                ]
                for col, ddl in migrations:
                    if col not in existing_cols:
                        conn.execute(ddl)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_updated_at ON chat_sessions(updated_at)"
                )
                # Feedback table — stores per-message thumbs up/down
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feedback (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id  TEXT NOT NULL,
                        message_id  TEXT NOT NULL,
                        rating      INTEGER NOT NULL CHECK(rating IN (-1, 1)),
                        comment     TEXT NOT NULL DEFAULT '',
                        created_at  TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id)"
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _prune_expired_sessions(self) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=_SESSION_TTL_DAYS)
        ).isoformat()
        with self._write_lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                result = conn.execute(
                    "DELETE FROM chat_sessions WHERE updated_at < ?", (cutoff,)
                )
                conn.execute("COMMIT")
                if result.rowcount:
                    logger.info(
                        "Pruned %d expired sessions (TTL %d days)",
                        result.rowcount,
                        _SESSION_TTL_DAYS,
                    )
            except Exception as exc:
                conn.execute("ROLLBACK")
                logger.warning("Session prune failed: %s", exc)

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionData:
        keys = row.keys()
        meta_raw = row["metadata"] if "metadata" in keys else "{}"
        try:
            metadata = json.loads(meta_raw or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return SessionData(
            session_id=row["session_id"],
            lead_profile=json.loads(row["lead_profile"]),
            conversation_history=json.loads(row["conversation_history"]),
            intent=row["intent"] if "intent" in keys else "",
            stage=row["stage"] if "stage" in keys else "discover",
            updated_at=row["updated_at"],
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def create_session(self) -> str:
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "INSERT INTO chat_sessions "
                    "(session_id, lead_profile, conversation_history, metadata, updated_at) "
                    "VALUES (?, '{}', '[]', '{}', ?)",
                    (sid, now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return sid

    def get(self, session_id: str) -> SessionData | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT session_id, lead_profile, conversation_history, "
            "intent, stage, updated_at, metadata "
            "FROM chat_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_session(row)

    def _create_session_with_id(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO chat_sessions "
                    "(session_id, lead_profile, conversation_history, metadata, updated_at) "
                    "VALUES (?, '{}', '[]', '{}', ?)",
                    (session_id, now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_or_create(self, session_id: str | None) -> SessionData:
        if session_id:
            existing = self.get(session_id)
            if existing:
                return existing
            # Use the client-supplied ID so save_turn can update the right row.
            self._create_session_with_id(session_id)
            data = self.get(session_id)
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
        data = self.get(session_id)
        if not data:
            data = SessionData(
                session_id,
                {},
                [],
                "",
                "discover",
                datetime.now(timezone.utc).isoformat(),
                {},
            )

        history = list(data.conversation_history)
        history.append({"role": "user", "content": user_message})
        # request_id ties this exact turn to the id the client uses for feedback
        # (POST /feedback message_id) — without it, feedback can only be matched
        # to a turn by guessing, which breaks on any multi-turn conversation.
        assistant_turn: dict[str, Any] = {"role": "assistant", "content": assistant_message}
        if request_id:
            assistant_turn["request_id"] = request_id
        history.append(assistant_turn)
        history = history[-_MAX_HISTORY_TURNS * 2 :]

        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "UPDATE chat_sessions "
                    "SET lead_profile = ?, conversation_history = ?, "
                    "intent = ?, stage = ?, updated_at = ? "
                    "WHERE session_id = ?",
                    (
                        json.dumps(lead_profile),
                        json.dumps(history),
                        intent,
                        stage,
                        now,
                        session_id,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

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
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "UPDATE chat_sessions SET metadata = ?, updated_at = ? WHERE session_id = ?",
                    (json.dumps(meta), now, session_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def patch_metadata(self, session_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        data = self.get(session_id)
        if not data:
            return
        meta = dict(data.metadata)
        meta.update(updates)
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "UPDATE chat_sessions SET metadata = ?, updated_at = ? WHERE session_id = ?",
                    (json.dumps(meta), now, session_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise


    # ------------------------------------------------------------------ #
    #  Feedback                                                            #
    # ------------------------------------------------------------------ #

    def save_feedback(
        self,
        session_id: str,
        message_id: str,
        rating: int,
        comment: str = "",
    ) -> None:
        """Save a thumbs-up (+1) or thumbs-down (-1) rating for a bot message."""
        if rating not in (1, -1):
            raise ValueError("rating must be 1 (positive) or -1 (negative)")
        now = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                # Upsert: one vote per (session_id, message_id) — last write wins
                conn.execute(
                    """
                    INSERT INTO feedback (session_id, message_id, rating, comment, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    (session_id, message_id, rating, comment or "", now),
                )
                # If the row already existed (ON CONFLICT skipped insert), update it
                conn.execute(
                    """
                    UPDATE feedback
                    SET rating = ?, comment = ?, created_at = ?
                    WHERE session_id = ? AND message_id = ?
                      AND created_at < ?
                    """,
                    (rating, comment or "", now, session_id, message_id, now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_feedback_stats(self) -> dict[str, Any]:
        """Return aggregate feedback totals across all sessions."""
        conn = self._connect()
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN rating = 1  THEN 1 ELSE 0 END) AS positive,
                SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) AS negative
            FROM feedback
            """
        ).fetchone()
        if not row:
            return {"total": 0, "positive": 0, "negative": 0, "score": 0.0}
        total    = row["total"]    or 0
        positive = row["positive"] or 0
        negative = row["negative"] or 0
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
        """Return most-recent feedback rows, optionally filtered by session_id."""
        limit = min(max(1, limit), 500)
        conn = self._connect()
        if session_id:
            rows = conn.execute(
                "SELECT session_id, message_id, rating, comment, created_at "
                "FROM feedback WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT session_id, message_id, rating, comment, created_at "
                "FROM feedback ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_leads(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent sessions with a captured name or email, newest first."""
        limit = min(max(1, limit), 500)
        conn = self._connect()
        rows = conn.execute(
            "SELECT session_id, lead_profile, intent, stage, updated_at "
            "FROM chat_sessions ORDER BY updated_at DESC LIMIT 1000"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                profile = json.loads(row["lead_profile"] or "{}")
            except json.JSONDecodeError:
                profile = {}
            if not isinstance(profile, dict):
                continue
            if not (profile.get("name") or profile.get("email")):
                continue
            out.append(
                {
                    "session_id": row["session_id"],
                    "lead_profile": profile,
                    "intent": row["intent"],
                    "stage": row["stage"],
                    "updated_at": row["updated_at"],
                }
            )
            if len(out) >= limit:
                break
        return out


class RedisSessionStore(SessionStoreBackend):
    def __init__(self, redis_url: str) -> None:
        from app.infra.redis_client import get_redis_client

        self._redis = get_redis_client(redis_url)
        self._ttl = _SESSION_TTL_SECONDS
        self._lock = threading.Lock()

    def _key(self, session_id: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{session_id}"

    @staticmethod
    def _empty_session(session_id: str) -> SessionData:
        return SessionData(
            session_id=session_id,
            lead_profile={},
            conversation_history=[],
            intent="",
            stage="discover",
            updated_at=datetime.now(timezone.utc).isoformat(),
            metadata={},
        )

    def _load(self, session_id: str) -> SessionData | None:
        raw = self._redis.get(self._key(session_id))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        self._redis.expire(self._key(session_id), self._ttl)
        return SessionData(
            session_id=session_id,
            lead_profile=payload.get("lead_profile") or {},
            conversation_history=payload.get("conversation_history") or [],
            intent=str(payload.get("intent") or ""),
            stage=str(payload.get("stage") or "discover"),
            updated_at=str(payload.get("updated_at") or ""),
            metadata=payload.get("metadata") or {},
        )

    def _save(self, data: SessionData) -> None:
        payload = {
            "lead_profile": data.lead_profile,
            "conversation_history": data.conversation_history,
            "intent": data.intent,
            "stage": data.stage,
            "updated_at": data.updated_at,
            "metadata": data.metadata,
        }
        key = self._key(data.session_id)
        self._redis.set(key, json.dumps(payload), ex=self._ttl)

    def create_session(self) -> str:
        sid = str(uuid.uuid4())
        self._save(self._empty_session(sid))
        return sid

    def get(self, session_id: str) -> SessionData | None:
        return self._load(session_id)

    def get_or_create(self, session_id: str | None) -> SessionData:
        if session_id:
            existing = self.get(session_id)
            if existing:
                return existing
            # Use the client-supplied ID so save_turn can find this key later.
            empty = self._empty_session(session_id)
            self._save(empty)
            return empty
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
        with self._lock:
            data = self.get(session_id) or self._empty_session(session_id)
            history = list(data.conversation_history)
            history.append({"role": "user", "content": user_message})
            assistant_turn: dict[str, Any] = {"role": "assistant", "content": assistant_message}
            if request_id:
                assistant_turn["request_id"] = request_id
            history.append(assistant_turn)
            history = history[-_MAX_HISTORY_TURNS * 2 :]
            data = SessionData(
                session_id=session_id,
                lead_profile=lead_profile,
                conversation_history=history,
                intent=intent,
                stage=stage,
                updated_at=datetime.now(timezone.utc).isoformat(),
                metadata=data.metadata,
            )
            self._save(data)

    def update_metadata(
        self,
        session_id: str,
        *,
        page_url: str = "",
        referrer: str = "",
    ) -> None:
        with self._lock:
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
            data = SessionData(
                session_id=data.session_id,
                lead_profile=data.lead_profile,
                conversation_history=data.conversation_history,
                intent=data.intent,
                stage=data.stage,
                updated_at=datetime.now(timezone.utc).isoformat(),
                metadata=meta,
            )
            self._save(data)

    def patch_metadata(self, session_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        with self._lock:
            data = self.get(session_id)
            if not data:
                return
            meta = dict(data.metadata)
            meta.update(updates)
            data = SessionData(
                session_id=data.session_id,
                lead_profile=data.lead_profile,
                conversation_history=data.conversation_history,
                intent=data.intent,
                stage=data.stage,
                updated_at=datetime.now(timezone.utc).isoformat(),
                metadata=meta,
            )
            self._save(data)

    # ------------------------------------------------------------------ #
    #  Feedback (Redis)                                                    #
    # ------------------------------------------------------------------ #
    # Keys:
    #   mobcoder:feedback:{session_id}:{message_id}  →  JSON record
    #   mobcoder:feedback:index                       →  sorted set (score=timestamp)

    _FB_KEY_PREFIX = "mobcoder:feedback:"
    _FB_INDEX_KEY  = "mobcoder:feedback:index"

    def _fb_key(self, session_id: str, message_id: str) -> str:
        return f"{self._FB_KEY_PREFIX}{session_id}:{message_id}"

    def save_feedback(
        self,
        session_id: str,
        message_id: str,
        rating: int,
        comment: str = "",
    ) -> None:
        """Persist thumbs-up (+1) / thumbs-down (-1). Upsert — last write wins."""
        if rating not in (1, -1):
            raise ValueError("rating must be 1 (positive) or -1 (negative)")
        import time as _time
        now_iso = datetime.now(timezone.utc).isoformat()
        now_ts  = _time.time()
        record = json.dumps({
            "session_id": session_id,
            "message_id": message_id,
            "rating":     rating,
            "comment":    comment or "",
            "created_at": now_iso,
        })
        fb_key = self._fb_key(session_id, message_id)
        pipe = self._redis.pipeline()
        pipe.set(fb_key, record, ex=self._ttl)
        pipe.zadd(self._FB_INDEX_KEY, {fb_key: now_ts})
        pipe.expire(self._FB_INDEX_KEY, self._ttl)
        pipe.execute()
        logger.debug("Redis feedback saved session=%s msg=%s rating=%s", session_id, message_id, rating)

    def list_feedback(
        self,
        limit: int = 50,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return most-recent feedback records, optionally filtered by session_id."""
        limit = min(max(1, limit), 500)
        keys = self._redis.zrevrange(self._FB_INDEX_KEY, 0, limit * 3 - 1)
        records: list[dict[str, Any]] = []
        for key in keys:
            raw = self._redis.get(key)
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if session_id and rec.get("session_id") != session_id:
                continue
            records.append(rec)
            if len(records) >= limit:
                break
        return records

    def get_feedback_stats(self) -> dict[str, Any]:
        """Aggregate thumbs totals from the index."""
        keys = self._redis.zrange(self._FB_INDEX_KEY, 0, -1)
        total = positive = negative = 0
        for key in keys:
            raw = self._redis.get(key)
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            total += 1
            if rec.get("rating") == 1:
                positive += 1
            elif rec.get("rating") == -1:
                negative += 1
        score = round(positive / total, 4) if total else 0.0
        return {"total": total, "positive": positive, "negative": negative, "score": score}

    def list_leads(self, limit: int = 100) -> list[dict[str, Any]]:
        """Scan session keys for sessions with a captured name or email.

        Redis has no secondary index over sessions, so this scans the
        session keyspace with SCAN (non-blocking) and filters in Python.
        Fine at pilot scale; revisit with a sorted-set index if volume grows.
        """
        limit = min(max(1, limit), 500)
        out: list[dict[str, Any]] = []
        try:
            cursor_iter = self._redis.scan_iter(match=f"{_REDIS_KEY_PREFIX}*", count=200)
        except Exception:
            return out
        for key in cursor_iter:
            raw = self._redis.get(key)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            profile = payload.get("lead_profile") or {}
            if not isinstance(profile, dict):
                continue
            if not (profile.get("name") or profile.get("email")):
                continue
            key_str = key.decode() if isinstance(key, bytes) else str(key)
            out.append(
                {
                    "session_id": key_str.removeprefix(_REDIS_KEY_PREFIX),
                    "lead_profile": profile,
                    "intent": str(payload.get("intent") or ""),
                    "stage": str(payload.get("stage") or "discover"),
                    "updated_at": str(payload.get("updated_at") or ""),
                }
            )
        out.sort(key=lambda r: r["updated_at"], reverse=True)
        return out[:limit]


# Backward-compatible alias
SessionStore = SQLiteSessionStore

_store: SessionStoreBackend | None = None
_store_lock = threading.Lock()


def _resolve_backend_name(backend: str | None) -> str:
    from app.config.settings import get_settings

    name = (backend or get_settings().session_store_backend or "sqlite").strip().lower()
    if name not in {"sqlite", "redis", "postgres"}:
        raise RuntimeError(
            f"Unsupported SESSION_STORE_BACKEND={name!r}; use sqlite, redis, or postgres"
        )
    return name


def get_session_store(
    db_path: str | None = None,
    *,
    backend: str | None = None,
    redis_url: str | None = None,
) -> SessionStoreBackend:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from app.config.settings import get_settings

                settings = get_settings()
                resolved = _resolve_backend_name(backend)
                if resolved == "redis":
                    url = redis_url or settings.redis_url
                    if not url:
                        raise RuntimeError(
                            "SESSION_STORE_BACKEND=redis requires REDIS_URL to be set"
                        )
                    _store = RedisSessionStore(url)
                    logger.info("Session store backend: redis")
                elif resolved == "postgres":
                    db_url = settings.database_url
                    if not db_url:
                        raise RuntimeError(
                            "SESSION_STORE_BACKEND=postgres requires DATABASE_URL to be set"
                        )
                    from app.infra.postgres import init_pool, run_migrations

                    if settings.auto_migrate_db:
                        run_migrations(db_url)
                    init_pool(
                        db_url,
                        min_size=settings.postgres_pool_min_size,
                        max_size=settings.postgres_pool_max_size,
                    )
                    from app.sessions.postgres_store import PostgresSessionStore

                    _store = PostgresSessionStore()
                    logger.info("Session store backend: postgres")
                else:
                    path = db_path or settings.session_db_path
                    _store = SQLiteSessionStore(path)
                    logger.info("Session store backend: sqlite (%s)", path)
    return _store


def reset_session_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None
    try:
        from app.infra.postgres import reset_postgres_pool_for_tests

        reset_postgres_pool_for_tests()
    except Exception:
        pass
