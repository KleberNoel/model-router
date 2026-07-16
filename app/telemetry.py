"""Optional self-hosted OpenTelemetry setup."""

import logging

from app.config import Settings

logger = logging.getLogger(__name__)


def configure_telemetry(app, settings: Settings) -> None:
    if not settings.otel_enabled:
        return
    if not settings.otel_exporter_otlp_endpoint:
        logger.warning("OTEL is enabled but MODEL_ROUTER_OTEL_EXPORTER_OTLP_ENDPOINT is empty")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        logger.warning("OTEL is enabled but telemetry dependencies are not installed")
