"""Celery 独立进程装配，当前不登记业务任务。"""

from typing import Any

from celery import Celery, signals
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.sdk.trace import TracerProvider

from creativity_service.core.config import Settings
from creativity_service.core.observability import configure_logging, create_tracer_provider

settings = Settings()
provider: TracerProvider | None = None

app = Celery("creativity_service")
app.conf.update(
    broker_url=settings.celery_broker_url.get_secret_value(),
    result_backend=settings.celery_result_url.get_secret_value(),
    broker_transport_options={"global_keyprefix": f"{settings.redis_key_prefix}:celery:broker:"},
    result_backend_transport_options={
        "global_keyprefix": f"{settings.redis_key_prefix}:celery:result:"
    },
    task_default_queue="creativity",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    result_expires=3600,
    enable_utc=True,
    timezone="UTC",
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
    worker_redirect_stdouts=False,
)


def setup_worker_logging(**_kwargs: Any) -> None:
    configure_logging(settings.log_level)


def start_tracing() -> None:
    global provider
    if provider is None:
        provider = create_tracer_provider(settings, "worker")
        CeleryInstrumentor().instrument(tracer_provider=provider)


def init_worker(sender: Any, **_kwargs: Any) -> None:
    # 单进程池没有子进程初始化信号；预派生池在各子进程内初始化导出线程。
    if not sender.pool_cls.__module__.endswith("prefork"):
        start_tracing()


def init_worker_process(**_kwargs: Any) -> None:
    start_tracing()


def stop_worker_tracing(**_kwargs: Any) -> None:
    global provider
    if provider is not None:
        CeleryInstrumentor().uninstrument()
        provider.shutdown()
        provider = None


signals.setup_logging.connect(setup_worker_logging)
signals.worker_init.connect(init_worker)
signals.worker_process_init.connect(init_worker_process)
signals.worker_shutdown.connect(stop_worker_tracing)
signals.worker_process_shutdown.connect(stop_worker_tracing)

# 用量任务以持久化请求为真值；重复定时唤醒由服务层事务去重。
app.conf.imports = tuple(app.conf.imports or ()) + ("creativity_service.modules.usage.tasks",)
app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "usage-sweep": {"task": "usage.sweep", "schedule": 60.0},
}

# 运行补偿独立于队列确认，数据库保存可重放的投递意图。
app.conf.imports = tuple(app.conf.imports or ()) + ("creativity_service.workers.runs",)
app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "runs-sweep": {"task": "runs.sweep", "schedule": 5.0},
}

# 远程连接健康检查按渠道恢复，短事务领取后再等待远端。
app.conf.imports = tuple(app.conf.imports or ()) + ("creativity_service.modules.mcp.tasks",)
app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "mcp-sweep": {"task": "mcp.sweep", "schedule": 30.0},
}

# 删除意图和到期内容共用可重放清理调度。
app.conf.imports = tuple(app.conf.imports or ()) + ("creativity_service.workers.cleanup",)
app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "cleanup-sweep": {"task": "cleanup.sweep", "schedule": 30.0},
}
