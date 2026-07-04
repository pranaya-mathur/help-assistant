from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    apify_api_token: str = ""
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    vector_db_type: str = "chroma"
    chroma_persist_dir: str = "./data/embeddings/chroma"
    chroma_collection: str = "mobcoder_sales"

    chunk_min_tokens: int = 300
    chunk_max_tokens: int = 700
    chunk_overlap_tokens: int = 75

    retrieval_top_k: int = 8
    reranker_top_k: int = 4
    reranker_backend: str = Field(default="heuristic", validation_alias="RERANKER_BACKEND")
    cohere_api_key: Optional[str] = Field(default=None, validation_alias="COHERE_API_KEY")
    cohere_rerank_model: str = Field(
        default="rerank-english-v3.0",
        validation_alias="COHERE_RERANK_MODEL",
    )
    cross_encoder_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        validation_alias="CROSS_ENCODER_MODEL",
    )
    query_rewrite_enabled: bool = Field(
        default=False,
        validation_alias="QUERY_REWRITE_ENABLED",
    )
    page_context_boost_enabled: bool = Field(
        default=True,
        validation_alias="PAGE_CONTEXT_BOOST_ENABLED",
    )
    llm_suggested_replies_enabled: bool = Field(
        default=True,
        validation_alias="LLM_SUGGESTED_REPLIES_ENABLED",
    )
    enable_otel: bool = Field(default=False, validation_alias="ENABLE_OTEL")
    otlp_endpoint: Optional[str] = Field(default=None, validation_alias="OTLP_ENDPOINT")
    retrieval_candidate_k: int = 16
    hybrid_retrieval_enabled: bool = Field(
        default=False,
        validation_alias="HYBRID_RETRIEVAL_ENABLED",
    )
    bm25_index_path: str = Field(
        default="./data/indexes/bm25_index.pkl",
        validation_alias="BM25_INDEX_PATH",
    )
    bm25_top_k: int = Field(default=20, validation_alias="BM25_TOP_K")
    vector_top_k: int = Field(default=20, validation_alias="VECTOR_TOP_K")
    hybrid_rrf_k: int = Field(default=60, validation_alias="HYBRID_RRF_K")
    hybrid_final_top_k: int = Field(default=8, validation_alias="HYBRID_FINAL_TOP_K")
    hybrid_min_bm25_score: float = Field(
        default=0.0,
        validation_alias="HYBRID_MIN_BM25_SCORE",
    )
    hybrid_fail_open: bool = Field(default=True, validation_alias="HYBRID_FAIL_OPEN")

    apify_actor_id: str = "apify/website-content-crawler"
    crawl_max_pages_per_seed: int = 100
    crawl_max_depth: int = 6
    # Stored as str in env to avoid pydantic-settings JSON-parsing comma lists
    crawl_allowed_domains: str = Field(
        default="mobcoder.ai,www.mobcoder.ai",
        validation_alias="CRAWL_ALLOWED_DOMAINS",
    )

    openai_max_retries: int = 4
    apify_max_retries: int = 3

    raw_data_dir: str = "./data/raw"
    chunks_data_dir: str = "./data/chunks"
    knowledge_md_path: str = "./docs/mobcoder_knowledge_base.md"
    system_prompt_path: str = "./prompts/mobcoder_sales_assistant_system_prompt.txt"

    api_host: str = "0.0.0.0"
    api_port: int = 8001
    api_reload: bool = Field(default=False, validation_alias="API_RELOAD")
    session_db_path: str = "./data/sessions/chat_sessions.db"
    session_store_backend: str = Field(
        default="sqlite",
        validation_alias="SESSION_STORE_BACKEND",
    )
    database_url: Optional[str] = Field(default=None, validation_alias="DATABASE_URL")
    auto_migrate_db: bool = Field(default=True, validation_alias="AUTO_MIGRATE_DB")
    persist_analytics_events: bool = Field(
        default=True,
        validation_alias="PERSIST_ANALYTICS_EVENTS",
    )
    postgres_pool_min_size: int = Field(default=1, validation_alias="POSTGRES_POOL_MIN_SIZE")
    postgres_pool_max_size: int = Field(default=10, validation_alias="POSTGRES_POOL_MAX_SIZE")
    rate_limit_enabled: bool = Field(default=True, validation_alias="RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=30, validation_alias="RATE_LIMIT_PER_MINUTE")
    rate_limit_chat_per_minute: Optional[int] = Field(
        default=None,
        validation_alias="RATE_LIMIT_CHAT_PER_MINUTE",
    )
    rate_limit_events_per_minute: Optional[int] = Field(
        default=None,
        validation_alias="RATE_LIMIT_EVENTS_PER_MINUTE",
    )
    rate_limit_escalate_per_minute: Optional[int] = Field(
        default=None,
        validation_alias="RATE_LIMIT_ESCALATE_PER_MINUTE",
    )
    rate_limit_feedback_per_minute: Optional[int] = Field(
        default=None,
        validation_alias="RATE_LIMIT_FEEDBACK_PER_MINUTE",
    )
    trust_proxy_headers: bool = Field(default=False, validation_alias="TRUST_PROXY_HEADERS")
    rate_limit_backend: str = Field(default="memory", validation_alias="RATE_LIMIT_BACKEND")
    redis_url: Optional[str] = Field(default=None, validation_alias="REDIS_URL")

    calendly_url: str = "https://calendly.com/hello-mobcoder/mobcoderai"
    contact_page_url: str = Field(
        default="https://mobcoder.ai/contact-us",
        validation_alias="CONTACT_PAGE_URL",
    )
    booking_cta: str = "Book a 30-minute discovery call with Mobcoder AI"
    eval_dataset_path: str = "./data/evals/golden_questions.json"

    internal_api_key: Optional[str] = Field(
        default=None,
        validation_alias="INTERNAL_API_KEY",
        description="Secret key for internal read-only endpoints (GET /feedback*). Set a long random string in prod.",
    )
    hubspot_webhook_url: Optional[str] = None
    google_chat_webhook_url: Optional[str] = Field(
        default=None,
        validation_alias="GOOGLE_CHAT_WEBHOOK_URL",
        description="Google Chat space incoming webhook for human-review lead alerts.",
    )
    google_sheets_webhook_url: Optional[str] = Field(
        default=None,
        validation_alias="GOOGLE_SHEETS_WEBHOOK_URL",
        description="Apps Script web-app URL that upserts qualified-lead rows into the lead-log Google Sheet.",
    )
    apollo_api_key: Optional[str] = None
    apollo_ip_enrichment_enabled: bool = Field(
        default=False,
        validation_alias="APOLLO_IP_ENRICHMENT_ENABLED",
        description="Enable async IP-to-company enrichment on new session creation.",
    )
    analytics_webhook_url: Optional[str] = None
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    enable_docs: Optional[bool] = Field(default=None, validation_alias="ENABLE_DOCS")
    enable_source_endpoints: Optional[bool] = Field(
        default=None,
        validation_alias="ENABLE_SOURCE_ENDPOINTS",
    )
    enable_debug_endpoints: Optional[bool] = Field(
        default=None,
        validation_alias="ENABLE_DEBUG_ENDPOINTS",
    )
    expose_internal_sales_metadata: bool = Field(
        default=False,
        validation_alias="EXPOSE_INTERNAL_SALES_METADATA",
    )

    cors_allowed_origins: str = Field(
        default="https://mobcoder.ai,https://www.mobcoder.ai,https://devweb-agent.mobcoder.ai,http://localhost:3000,http://127.0.0.1:7860,http://127.0.0.1:8765,http://localhost:8765",
        validation_alias="CORS_ALLOWED_ORIGINS",
    )
    enable_llm_grounding: bool = True
    # NOTE: "sovereign" is currently a stub that delegates straight to the heuristic
    # provider (see app/agent/grounding.py) — there is no functional difference
    # between the two values today. The real on/off switch for the LLM-based claim
    # check is `enable_llm_grounding` above (already True by default), which both
    # providers honor. Left as "heuristic" to avoid implying a capability that
    # doesn't exist yet; rename/implement SovereignGroundingProvider before using
    # "sovereign" to mean something different in config.
    grounding_provider: str = "heuristic"  # heuristic | sovereign (see note above)
    ingest_manifest_path: str = "./data/formatted/ingest_manifest.json"
    eval_min_pass_rate: float = 0.90
    enable_chat_streaming: bool = True
    enable_lead_qualification: bool = Field(
        default=True,
        validation_alias="ENABLE_LEAD_QUALIFICATION",
    )
    operating_mode: Literal["help", "sales", "full"] = Field(
        default="full",
        validation_alias="OPERATING_MODE",
    )
    grounding_replace_on_failure: bool = Field(
        default=False,
        validation_alias="GROUNDING_REPLACE_ON_FAILURE",
    )
    trust_client_conversation_history: bool = Field(
        default=False,
        validation_alias="TRUST_CLIENT_CONVERSATION_HISTORY",
        description=(
            "If false (default), request.conversation_history is ignored and the "
            "server-persisted session history is always used instead — a client "
            "cannot inject fabricated prior turns to manipulate intent/lead "
            "extraction/CRM qualification. Enable only for stateless callers that "
            "deliberately don't use the server session store (e.g. local scripting)."
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in str(self.cors_allowed_origins).split(",") if o.strip()]

    def allowed_domains_list(self) -> list[str]:
        if isinstance(self.crawl_allowed_domains, list):
            return self.crawl_allowed_domains  # type: ignore[return-value]
        return [d.strip() for d in str(self.crawl_allowed_domains).split(",") if d.strip()]

    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"prod", "production"}

    def is_help_mode(self) -> bool:
        return self.operating_mode.strip().lower() == "help"

    def is_sales_layer_enabled(self) -> bool:
        return self.operating_mode.strip().lower() in {"sales", "full"}

    def docs_enabled(self) -> bool:
        if self.enable_docs is not None:
            return self.enable_docs
        return not self.is_production()

    def source_endpoints_enabled(self) -> bool:
        if self.enable_source_endpoints is not None:
            return self.enable_source_endpoints
        return not self.is_production()

    def debug_endpoints_enabled(self) -> bool:
        if self.enable_debug_endpoints is not None:
            return self.enable_debug_endpoints
        return not self.is_production()

    def require_openai_key(self) -> str:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to your .env file.")
        return self.openai_api_key

    def require_apify_token(self) -> str:
        if not self.apify_api_token:
            raise RuntimeError("APIFY_API_TOKEN is not set. Add it to your .env file.")
        return self.apify_api_token


@lru_cache
def get_settings() -> Settings:
    return Settings()
