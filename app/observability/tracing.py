from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_tracer: Any = None
_initialized = False


def _init_tracer() -> Any:
    global _tracer, _initialized
    if _initialized:
        return _tracer
    _initialized = True
    settings = get_settings()
    if not settings.enable_otel:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )

        resource = Resource.create({"service.name": "mobcoder-sales-assistant"})
        provider = TracerProvider(resource=resource)
        if settings.otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
            )
        else:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("mobcoder.agent")
        logger.info("OpenTelemetry tracing enabled")
    except Exception as exc:
        logger.warning("OpenTelemetry init failed; tracing disabled: %s", exc)
        _tracer = None
    return _tracer


def get_tracer() -> Any:
    return _init_tracer()


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    from opentelemetry import trace

    with tracer.start_as_current_span(name) as current:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise


def instrument_fastapi(app: Any) -> None:
    settings = get_settings()
    if not settings.enable_otel:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI OpenTelemetry instrumentation enabled")
    except Exception as exc:
        logger.warning("FastAPI OTel instrumentation skipped: %s", exc)
