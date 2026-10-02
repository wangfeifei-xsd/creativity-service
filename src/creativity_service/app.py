"""应用工厂，不在导入或启动阶段创建业务表。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.middleware.cors import CORSMiddleware

from creativity_service import __version__
from creativity_service.api.errors import ErrorResponse, register_error_handlers
from creativity_service.api.health import router as health_router
from creativity_service.api.middleware import RequestContextMiddleware
from creativity_service.api.routes import admin_router, api_router
from creativity_service.core.artifacts import S3ObjectStore
from creativity_service.core.config import Settings
from creativity_service.core.contracts.export import schemas
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.observability import configure_logging, create_tracer_provider
from creativity_service.core.services import build_core_services
from creativity_service.modules.agents.assembly import build_agent_service
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.conversations.assembly import build_conversation_service
from creativity_service.modules.integrations.assembly import build_integration_services
from creativity_service.modules.mcp.assembly import build_mcp_service
from creativity_service.modules.memory.assembly import build_memory_service
from creativity_service.modules.models.assembly import build_model_services
from creativity_service.modules.prompts.api import register_prompt_errors
from creativity_service.modules.prompts.assembly import (
    build_prompt_service,
    register_prompt_cleanup,
)
from creativity_service.modules.runs.assembly import build_run_service
from creativity_service.modules.skills.assembly import build_skill_service, register_skill_cleanup
from creativity_service.modules.tools.assembly import build_tool_services, register_tool_cleanup
from creativity_service.modules.usage.assembly import build_usage_services


class PlatformAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            schema = get_openapi(title=self.title, version=self.version, routes=self.routes)
            for path_name, path in schema["paths"].items():
                for operation in path.values():
                    if path_name.startswith("/api/v1/") and path_name != "/api/v1/auth/token":
                        operation.setdefault("parameters", []).append(
                            {
                                "name": "X-Business-Delegation",
                                "in": "header",
                                "required": True,
                                "description": (
                                    "业务后端签发并绑定实际请求的 business-delegation-v1 委托"
                                ),
                                "schema": {"type": "string", "maxLength": 16384},
                            }
                        )
                    for response in operation.get("responses", {}).values():
                        response.setdefault("headers", {})["X-Request-ID"] = {
                            "description": "请求标识",
                            "schema": {"type": "string"},
                        }
            schema.setdefault("components", {}).setdefault("schemas", {}).update(
                schemas("#/components/schemas/{model}")
            )
            self.openapi_schema = schema
        return self.openapi_schema


def create_schema_app() -> FastAPI:
    """离线契约导出共用同一路由装配，不加载环境或外部连接。"""
    app = PlatformAPI(
        title="一玄智能平台",
        version=__version__,
        responses={
            status: {"model": ErrorResponse}
            for status in (400, 401, 403, 404, 405, 409, 422, 429, 500, 503)
        },
    )
    register_error_handlers(app)
    register_prompt_errors(app)
    app.include_router(health_router)
    app.include_router(admin_router)
    app.include_router(api_router)

    return app


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    provider = create_tracer_provider(settings, "api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        infrastructure = Infrastructure(settings)
        app.state.infrastructure = infrastructure
        app.state.iam, app.state.channels = build_channel_services(
            infrastructure.engine,
            infrastructure.redis_clients["redis_auth"],
            settings.redis_key_prefix,
            management_ttl=settings.management_token_ttl,
            service_ttl=settings.service_token_ttl,
        )
        app.state.prompts = build_prompt_service(
            infrastructure.engine,
            app.state.iam.authorization,
            S3ObjectStore(infrastructure.s3, infrastructure.bucket),
        )
        app.state.tools = build_tool_services(
            infrastructure.engine,
            app.state.iam.authorization,
            redis=infrastructure.redis_clients["redis_cache"],
            prefix=settings.redis_key_prefix,
        )
        app.state.skills = build_skill_service(
            infrastructure.engine,
            app.state.iam.authorization,
            S3ObjectStore(infrastructure.s3, infrastructure.bucket),
            app.state.tools.management,
        )
        app.state.usage = build_usage_services(
            infrastructure.engine,
            app.state.channels.channels,
            S3ObjectStore(infrastructure.s3, infrastructure.bucket),
        )
        app.state.mcp = build_mcp_service(
            infrastructure.engine, app.state.iam.authorization, app.state.tools
        )
        app.state.integrations = build_integration_services(
            infrastructure.engine, app.state.iam.authorization
        )
        app.state.delegation = app.state.integrations.delegation
        app.state.authentication = app.state.iam.authentication
        app.state.core = build_core_services(
            infrastructure.engine,
            S3ObjectStore(infrastructure.s3, infrastructure.bucket),
            authorization=app.state.iam.authorization,
            configure_cleanup=lambda registry: register_prompt_cleanup(
                registry, infrastructure.engine, app.state.iam.authorization
            ),
        )
        register_tool_cleanup(
            app.state.core.cleanup, infrastructure.engine, app.state.iam.authorization
        )
        register_skill_cleanup(app.state.core.cleanup, app.state.skills)
        app.state.usage.exports.register_cleanup(app.state.core.cleanup)
        app.state.models = build_model_services(
            infrastructure.engine,
            app.state.iam,
            cleanup=app.state.core.cleanup,
            prices=app.state.usage.prices,
        )
        app.state.runs = build_run_service(
            infrastructure.engine,
            app.state.iam,
            app.state.core.versions,
            app.state.usage.budgets,
            app.state.usage.ledger,
            cleanup=app.state.core.cleanup,
        )
        app.state.conversations = build_conversation_service(
            infrastructure.engine,
            app.state.iam.authorization,
            app.state.runs,
            S3ObjectStore(infrastructure.s3, infrastructure.bucket),
            app.state.core.cleanup,
        )
        app.state.memory = build_memory_service(
            infrastructure.engine, app.state.iam.authorization, app.state.core.cleanup
        )
        app.state.agents = build_agent_service(
            infrastructure.engine,
            app.state.iam.authorization,
            app.state.tools.management,
            app.state.skills,
            app.state.usage.budgets,
        )
        try:
            yield
        finally:
            try:
                await infrastructure.close()
            finally:
                provider.shutdown()

    app = create_schema_app()
    app.router.lifespan_context = lifespan
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    return app
