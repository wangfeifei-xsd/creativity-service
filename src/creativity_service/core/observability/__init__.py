"""结构化日志及进程级追踪配置。"""

import json
import logging
import os
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Literal

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from creativity_service import __version__
from creativity_service.core.config import Settings
from creativity_service.core.context import current_context

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span = trace.get_current_span().get_span_context()
        data: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        context = current_context.get()
        if context is not None:
            data.update(channel_id=context.scope.channel_id, environment=context.scope.environment)
        if span.is_valid:
            data.update(trace_id=f"{span.trace_id:032x}", span_id=f"{span.span_id:016x}")
        # 仅收录显式允许的诊断字段，避免请求正文、凭据及异常文本进入日志。
        for key in (
            "method",
            "route",
            "status_code",
            "duration_ms",
            "error_type",
            "error_location",
        ):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, ensure_ascii=False)


def configure_logging(
    settings: Settings,
    role: Literal["api", "worker", "scheduler", "launcher"] = "api",
    *,
    child_process: bool = False,
) -> None:
    settings.log_directory.mkdir(parents=True, exist_ok=True)
    # 预派生 Worker 的子进程独立轮转，避免多个进程同时重命名同一日志文件。
    suffix = f"-{os.getpid()}" if child_process else ""
    file_handler = RotatingFileHandler(
        settings.log_directory / f"{role}{suffix}.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(), file_handler]
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    if role == "launcher":
        handlers[0].setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    for previous in root.handlers[:]:
        root.removeHandler(previous)
        previous.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(settings.log_level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


def create_tracer_provider(settings: Settings, role: Literal["api", "worker"]) -> TracerProvider:
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": f"{settings.service_name}-{role}",
                "service.version": __version__,
                "deployment.environment.name": settings.environment,
            }
        )
    )
    if settings.otel_enabled:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces",
                    timeout=5,
                )
            )
        )
    return provider
