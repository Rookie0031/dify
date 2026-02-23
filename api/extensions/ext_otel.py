import atexit
import logging
import os
import platform
import socket
from typing import Union

from configs import dify_config
from dify_app import DifyApp

logger = logging.getLogger(__name__)


def _parse_header_pairs(raw_headers: str) -> dict[str, str]:
    """
    Parse headers from "k1=v1,k2=v2" format.
    """
    parsed: dict[str, str] = {}
    for item in (raw_headers or "").split(","):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            parsed[key] = value
    return parsed


def _build_http_headers(*, signal_headers: str, common_headers: str, api_key: str) -> dict[str, str] | None:
    headers = _parse_header_pairs(common_headers)
    headers.update(_parse_header_pairs(signal_headers))
    if headers:
        return headers
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return None


def _build_grpc_headers(
    *,
    signal_headers: str,
    common_headers: str,
    api_key: str,
) -> tuple[tuple[str, str], ...] | None:
    headers = _parse_header_pairs(common_headers)
    headers.update(_parse_header_pairs(signal_headers))
    if headers:
        # gRPC metadata keys are expected to be lowercase.
        return tuple((key.lower(), value) for key, value in headers.items())
    if api_key:
        return (("authorization", f"Bearer {api_key}"),)
    return None


def init_app(app: DifyApp):
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as GRPCMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GRPCSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter as HTTPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HTTPSpanExporter
    from opentelemetry.metrics import set_meter_provider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
    from opentelemetry.semconv.resource import ResourceAttributes
    from opentelemetry.trace import set_tracer_provider

    from extensions.otel.instrumentation import init_instruments
    from extensions.otel.runtime import setup_context_propagation, shutdown_tracer

    setup_context_propagation()
    # Initialize OpenTelemetry
    # Follow Semantic Convertions 1.32.0 to define resource attributes
    resource = Resource(
        attributes={
            ResourceAttributes.SERVICE_NAME: dify_config.APPLICATION_NAME,
            ResourceAttributes.SERVICE_VERSION: f"dify-{dify_config.project.version}-{dify_config.COMMIT_SHA}",
            ResourceAttributes.PROCESS_PID: os.getpid(),
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: f"{dify_config.DEPLOY_ENV}-{dify_config.EDITION}",
            ResourceAttributes.HOST_NAME: socket.gethostname(),
            ResourceAttributes.HOST_ARCH: platform.machine(),
            "custom.deployment.git_commit": dify_config.COMMIT_SHA,
            ResourceAttributes.HOST_ID: platform.node(),
            ResourceAttributes.OS_TYPE: platform.system().lower(),
            ResourceAttributes.OS_DESCRIPTION: platform.platform(),
            ResourceAttributes.OS_VERSION: platform.version(),
        }
    )
    sampler = ParentBasedTraceIdRatio(dify_config.OTEL_SAMPLING_RATE)
    provider = TracerProvider(resource=resource, sampler=sampler)

    set_tracer_provider(provider)
    exporter: Union[GRPCSpanExporter, HTTPSpanExporter, ConsoleSpanExporter]
    metric_exporter: Union[GRPCMetricExporter, HTTPMetricExporter, ConsoleMetricExporter]
    protocol = (dify_config.OTEL_EXPORTER_OTLP_PROTOCOL or "").lower()
    if dify_config.OTEL_EXPORTER_TYPE == "otlp":
        if protocol == "grpc":
            trace_headers = _build_grpc_headers(
                signal_headers=dify_config.OTLP_TRACE_HEADERS,
                common_headers=dify_config.OTLP_HEADERS,
                api_key=dify_config.OTLP_API_KEY,
            )
            exporter = GRPCSpanExporter(
                endpoint=dify_config.OTLP_BASE_ENDPOINT,
                headers=trace_headers,
                insecure=True,
            )
            metric_headers = _build_grpc_headers(
                signal_headers=dify_config.OTLP_METRIC_HEADERS,
                common_headers=dify_config.OTLP_HEADERS,
                api_key=dify_config.OTLP_API_KEY,
            )
            metric_exporter = GRPCMetricExporter(
                endpoint=dify_config.OTLP_BASE_ENDPOINT,
                headers=metric_headers,
                insecure=True,
            )
        else:
            trace_headers = _build_http_headers(
                signal_headers=dify_config.OTLP_TRACE_HEADERS,
                common_headers=dify_config.OTLP_HEADERS,
                api_key=dify_config.OTLP_API_KEY,
            )
            trace_endpoint = dify_config.OTLP_TRACE_ENDPOINT
            if not trace_endpoint:
                trace_endpoint = dify_config.OTLP_BASE_ENDPOINT + "/v1/traces"
            exporter = HTTPSpanExporter(
                endpoint=trace_endpoint,
                headers=trace_headers,
            )

            metric_headers = _build_http_headers(
                signal_headers=dify_config.OTLP_METRIC_HEADERS,
                common_headers=dify_config.OTLP_HEADERS,
                api_key=dify_config.OTLP_API_KEY,
            )
            metric_endpoint = dify_config.OTLP_METRIC_ENDPOINT
            if not metric_endpoint:
                metric_endpoint = dify_config.OTLP_BASE_ENDPOINT + "/v1/metrics"
            metric_exporter = HTTPMetricExporter(
                endpoint=metric_endpoint,
                headers=metric_headers,
            )
    else:
        exporter = ConsoleSpanExporter()
        metric_exporter = ConsoleMetricExporter()

    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_queue_size=dify_config.OTEL_MAX_QUEUE_SIZE,
            schedule_delay_millis=dify_config.OTEL_BATCH_EXPORT_SCHEDULE_DELAY,
            max_export_batch_size=dify_config.OTEL_MAX_EXPORT_BATCH_SIZE,
            export_timeout_millis=dify_config.OTEL_BATCH_EXPORT_TIMEOUT,
        )
    )
    reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=dify_config.OTEL_METRIC_EXPORT_INTERVAL,
        export_timeout_millis=dify_config.OTEL_METRIC_EXPORT_TIMEOUT,
    )
    set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    init_instruments(app)

    atexit.register(shutdown_tracer)


def is_enabled():
    return dify_config.ENABLE_OTEL
