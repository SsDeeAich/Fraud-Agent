"""OpenTelemetry Observability and Tracing module for the Compliance Agent.

This module initializes OpenTelemetry (OTel) tracing with Google Cloud Trace integration
and provides decorators and context managers to instrument LLM agent workflows,
sub-agent execution, asynchronous BigQuery tools, GCS grounding operations, and
the 8-step fraud investigation pipeline.
"""

import functools
import inspect
import logging
import os
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import StatusCode

logger = logging.getLogger("compliance_agent.telemetry")

_TELEMETRY_INITIALIZED = False


def setup_telemetry(
    service_name: Optional[str] = None,
    service_version: str = "1.0.0",
    project_id: Optional[str] = None,
    exporter_type: Optional[str] = None,
) -> trace.Tracer:
    """Initializes OpenTelemetry TracerProvider and registers span exporters.

    Args:
        service_name: Name of the agent service (defaults to OTEL_SERVICE_NAME or 'compliance_agent').
        service_version: Version of the agent service.
        project_id: Google Cloud Project ID (defaults to GOOGLE_CLOUD_PROJECT).
        exporter_type: Type of exporter ('gcp_trace', 'console', or 'none'). Defaults to env OTEL_TRACES_EXPORTER or 'gcp_trace'.

    Returns:
        The configured OpenTelemetry Tracer instance.
    """
    global _TELEMETRY_INITIALIZED
    if _TELEMETRY_INITIALIZED:
        return trace.get_tracer(service_name or "compliance_agent")

    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "compliance_agent")
    project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "customers-444618")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    exporter_type = (exporter_type or os.getenv("OTEL_TRACES_EXPORTER", "gcp_trace")).lower()

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "service.namespace": "fraud_detection",
            "cloud.provider": "gcp",
            "cloud.account.id": project_id,
            "cloud.region": location,
            "gen_ai.system": "google_adk",
        }
    )

    provider = TracerProvider(resource=resource)

    if exporter_type == "gcp_trace":
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            exporter = CloudTraceSpanExporter(project_id=project_id)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("Initialized OpenTelemetry with Google Cloud Trace exporter for project=%s", project_id)
        except Exception as e:
            logger.warning(
                "Could not initialize CloudTraceSpanExporter (%s). Falling back to ConsoleSpanExporter.",
                e,
            )
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter_type == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("Initialized OpenTelemetry with ConsoleSpanExporter")
    else:
        logger.info("OpenTelemetry exporter set to '%s' (no span processor added).", exporter_type)

    trace.set_tracer_provider(provider)
    _TELEMETRY_INITIALIZED = True
    return trace.get_tracer(service_name)


def get_tracer(name: str = "compliance_agent") -> trace.Tracer:
    """Returns an OpenTelemetry Tracer instance."""
    return trace.get_tracer(name)


def _format_attribute_value(val: Any) -> Any:
    """Sanitizes an attribute value to a type acceptable by OpenTelemetry span attributes."""
    if isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, (list, tuple)):
        # Limit long lists for telemetry attributes
        if len(val) > 20:
            return str(val[:20]) + f"... ({len(val)} items)"
        return str(val)
    if isinstance(val, dict):
        return str(val)[:500]
    return str(val)


def trace_tool(
    name: Optional[str] = None,
    tool_type: str = "tool",
    capture_args: bool = True,
) -> Callable:
    """Decorator to instrument synchronous or asynchronous agent tools with OpenTelemetry spans.

    Args:
        name: Custom span name (defaults to function name prefixed with 'tool.').
        tool_type: Category of tool (e.g. 'bigquery', 'gcs', 'pipeline').
        capture_args: Whether to record function input arguments as span attributes.

    Returns:
        Decorated function with automatic span tracking, attribute recording, and error reporting.
    """
    def decorator(func: Callable) -> Callable:
        span_name = name or f"tool.{tool_type}.{func.__name__}"

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                tracer = get_tracer()
                with tracer.start_as_current_span(span_name) as span:
                    span.set_attribute("gen_ai.tool.name", func.__name__)
                    span.set_attribute("gen_ai.tool.type", tool_type)

                    if capture_args:
                        # Capture kwargs
                        for k, v in kwargs.items():
                            span.set_attribute(f"tool.input.{k}", _format_attribute_value(v))
                        # Capture positional args via signature
                        try:
                            sig = inspect.signature(func)
                            bound_args = sig.bind_partial(*args, **kwargs)
                            for k, v in bound_args.arguments.items():
                                span.set_attribute(f"tool.input.{k}", _format_attribute_value(v))
                        except Exception:
                            pass

                    try:
                        result = await func(*args, **kwargs)
                        span.set_status(StatusCode.OK)
                        span.add_event("tool.execution_completed")
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(StatusCode.ERROR, description=str(exc))
                        logger.error("Error in tool span '%s': %s", span_name, exc)
                        raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute("gen_ai.tool.name", func.__name__)
                span.set_attribute("gen_ai.tool.type", tool_type)

                if capture_args:
                    for k, v in kwargs.items():
                        span.set_attribute(f"tool.input.{k}", _format_attribute_value(v))
                    try:
                        sig = inspect.signature(func)
                        bound_args = sig.bind_partial(*args, **kwargs)
                        for k, v in bound_args.arguments.items():
                            span.set_attribute(f"tool.input.{k}", _format_attribute_value(v))
                    except Exception:
                        pass

                try:
                    result = func(*args, **kwargs)
                    span.set_status(StatusCode.OK)
                    span.add_event("tool.execution_completed")
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, description=str(exc))
                    logger.error("Error in tool span '%s': %s", span_name, exc)
                    raise

        return sync_wrapper

    return decorator


@contextmanager
def trace_span(span_name: str, attributes: Optional[Dict[str, Any]] = None):
    """Context manager to create a child span for workflow or pipeline stages.

    Usage:
        with trace_span("pipeline.step_1_account_graph", {"publisher_id": publisher_id}):
            # execute step
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(span_name) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, _format_attribute_value(v))
        try:
            yield span
            span.set_status(StatusCode.OK)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, description=str(exc))
            raise
