import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.api.routes import router
from app.config.settings import get_settings
from app.middleware.rate_limit import RateLimitMiddleware
from app.observability.tracing import instrument_fastapi
from app.rag.vector_store import get_vector_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _verify_postgres_backend(settings) -> None:
    backend = settings.session_store_backend.strip().lower()
    needs_pool = backend == "postgres" or (
        bool(settings.database_url) and settings.persist_analytics_events
    )
    if not needs_pool:
        return
    if not settings.database_url:
        raise RuntimeError("SESSION_STORE_BACKEND=postgres requires DATABASE_URL")

    from app.infra.postgres import init_pool, run_migrations, verify_postgres_connectivity

    verify_postgres_connectivity(settings.database_url)
    if settings.auto_migrate_db:
        applied = run_migrations(settings.database_url)
        if applied:
            logger.info("Postgres migrations applied: %s", ", ".join(applied))
    init_pool(
        settings.database_url,
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
    )
    logger.info("Postgres connectivity verified")


def _verify_redis_backends(settings) -> None:
    redis_url = settings.redis_url
    if not redis_url:
        return
    needs_redis = (
        settings.session_store_backend.strip().lower() == "redis"
        or settings.rate_limit_backend.strip().lower() == "redis"
    )
    if not needs_redis:
        return
    from app.infra.redis_client import verify_redis_connectivity

    verify_redis_connectivity(redis_url)
    logger.info("Redis connectivity verified for production backends")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    try:
        _verify_postgres_backend(settings)
        _verify_redis_backends(settings)
    except Exception as exc:
        logger.error("Datastore startup check failed: %s", exc)
        raise
    try:
        count = get_vector_store().count()
        if count <= 0:
            logger.warning(
                "Vector store is empty — using bundled seed knowledge. "
                "Run: python scripts/seed_knowledge.py --ingest (or crawl + ingest)."
            )
        elif count < 100:
            logger.warning(
                "Vector store has only %s chunks (health target is 100+) — "
                "answers will be thin until a full crawl + ingest runs. "
                "Run: python scripts/crawl_mobcoder.py --use-sitemap --max-pages 500 "
                "&& python scripts/ingest.py --reset-collection",
                count,
            )
        else:
            logger.info("Vector store ready: %s chunks", count)
    except Exception as exc:
        logger.error("Vector store unavailable at startup: %s", exc)
    if not (settings.internal_api_key or "").strip():
        logger.warning(
            "INTERNAL_API_KEY is unset — /feedback, /feedback/stats, /leads, and "
            "/admin are all effectively disabled (endpoints 404 / requests fail). "
            "Set INTERNAL_API_KEY to enable feedback and leads visibility."
        )
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Mobcoder AI Sales & Help Assistant",
        description="RAG-powered sales and help chatbot for mobcoder.ai",
        version="1.0.0",
        docs_url="/docs" if settings.docs_enabled() else None,
        redoc_url="/redoc" if settings.docs_enabled() else None,
        openapi_url="/openapi.json" if settings.docs_enabled() else None,
        lifespan=_lifespan,
    )

    cors_kwargs: dict = {
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-Internal-Key", "X-Request-Id"],
    }
    if settings.is_production():
        cors_kwargs["allow_origins"] = settings.cors_origins_list()
    else:
        # Local dev: widget demo may be opened via file:// (Origin: null) or any localhost port.
        cors_kwargs["allow_origins"] = settings.cors_origins_list() + ["null"]
        cors_kwargs["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
    app.add_middleware(CORSMiddleware, **cors_kwargs)
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=settings.rate_limit_per_minute,
        chat_requests_per_minute=settings.rate_limit_chat_per_minute,
        events_requests_per_minute=settings.rate_limit_events_per_minute,
        escalate_requests_per_minute=settings.rate_limit_escalate_per_minute,
        feedback_requests_per_minute=settings.rate_limit_feedback_per_minute,
        enabled=settings.rate_limit_enabled,
        trust_proxy_headers=settings.trust_proxy_headers,
        backend=settings.rate_limit_backend,
        redis_url=settings.redis_url,
    )

    app.include_router(router, prefix="/api/v1")
    instrument_fastapi(app)

    _admin_html_path = Path(__file__).parent / "app" / "static" / "admin.html"

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    async def admin_portal():
        """Minimal read-only ops portal: leads + feedback.

        The page itself has no gate (it's static HTML with no data baked in);
        every data call it makes requires INTERNAL_API_KEY via X-Internal-Key,
        same as GET /api/v1/feedback and /api/v1/leads.
        """
        if not _admin_html_path.exists():
            raise HTTPException(status_code=404, detail="Not found")
        return HTMLResponse(content=_admin_html_path.read_text(encoding="utf-8"))

    @app.get("/")
    async def root():
        payload = {
            "service": "MobCoder Sales & Help Assistant",
            "health": "/api/v1/health",
            "admin": "/admin",
        }
        if settings.docs_enabled():
            payload["docs"] = "/docs"
        return payload

    return app


app = create_app()

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )
