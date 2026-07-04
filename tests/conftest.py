import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("APIFY_API_TOKEN", "test_apify_token")
os.environ.setdefault("OPENAI_API_KEY", "test_openai_key")
os.environ.setdefault("CHROMA_PERSIST_DIR", "/tmp/mobcoder_test_chroma")
os.environ.setdefault("SESSION_STORE_BACKEND", "sqlite")
os.environ.setdefault("RATE_LIMIT_BACKEND", "memory")

# Clear cached settings between tests
import app.config.settings as _settings_mod


@pytest.fixture(autouse=True)
def _reset_stores():
    from app.sessions.store import reset_session_store_for_tests
    from app.infra.redis_client import reset_redis_clients_for_tests

    reset_session_store_for_tests()
    reset_redis_clients_for_tests()
    yield
    reset_session_store_for_tests()
    reset_redis_clients_for_tests()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    _settings_mod.get_settings.cache_clear()
    yield
    _settings_mod.get_settings.cache_clear()


_settings_mod.get_settings.cache_clear()
