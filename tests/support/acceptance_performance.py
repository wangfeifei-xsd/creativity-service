"""性能测量的真实 HTTP 与四渠道主体复核装配；仅使用隔离测试种子。"""

import asyncio
import base64
import socket
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx
import uvicorn

from creativity_service.core.primitives import utcnow
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.modules.integrations.assembly import build_integration_services
from creativity_service.modules.integrations.schemas import DelegationKeyCreate
from creativity_service.modules.integrations.subject_contracts import SubjectReviewSave
from creativity_service.modules.mcp.assembly import build_mcp_service
from creativity_service.modules.mcp.schemas import McpCreate, McpCredential, McpDiscovery
from examples.backend.client import BusinessBackendClient, Principal
from examples.mcp.server import serve
from tests.integration.channels.conftest import credential
from tests.integration.integrations.conftest import TestKeys


class Principals:
    def __init__(self, value):
        self.value = value

    async def current(self):
        return self.value


@asynccontextmanager
async def http_clients(env, tenants):
    app = env.client._transport.app
    with ExitStack() as sources, socket.socket() as sock:
        outbound = OutboundPolicy(())
        mcp = build_mcp_service(
            env.engine, env.iam.authorization, env.tools, outbound=outbound
        )
        bundle = build_integration_services(
            env.engine, env.iam.authorization, provider=TestKeys(), mcp=mcp
        )
        app.state.mcp, app.state.integrations, app.state.delegation = mcp, bundle, bundle.delegation
        identities = []
        for tenant in tenants:
            context = tenant.manager.context
            source = sources.enter_context(serve())
            source.scope = context.scope.model_copy(
                update={"subject_type": "member", "subject_id": "same-performance-user"}
            ).model_dump()
            identity = await credential(env, tenant, "性能测量后端")
            principal = Principal(
                "member",
                "same-performance-user",
                ["run:create", "run:read"],
                {kind: ["*"] for kind in ("agent", "model", "model_route", "prompt", "run")},
            )
            source.permissions[principal.subject_id] = {
                "actions": principal.actions,
                "resources": principal.resources,
            }
            outbound.destinations += (
                Destination(
                    context.scope.channel_id,
                    "test",
                    "mcp",
                    "127.0.0.1",
                    port=source.port,
                    scheme="http",
                    allowed_networks=("127.0.0.1/32",),
                ),
            )
            connection = await mcp.create(
                context, McpCreate(name="性能测量主体复核", endpoint=source.endpoint)
            )
            await mcp.rotate(
                context,
                connection.connection_id,
                McpCredential(revision=connection.revision, token=source.token),
            )
            discovery = await mcp.probe(context, connection.connection_id, True)
            assert isinstance(discovery, McpDiscovery)
            current = await mcp.detail(context, connection.connection_id)
            await mcp.set_enabled(
                context, connection.connection_id, current.connection.revision, True
            )
            await bundle.subject_review.save(
                context,
                SubjectReviewSave(
                    client_id=identity.client.client_id,
                    connection_id=connection.connection_id,
                    discovery_id=discovery.discovery_id,
                    remote_tool_name="access.review-current",
                    timeout_seconds=30,
                ),
            )
            key = await bundle.keys.create(
                context,
                DelegationKeyCreate(
                    client_id=identity.client.client_id,
                    issuer="performance.example",
                    audience="creativity-api",
                    expires_at=utcnow() + timedelta(hours=1),
                ),
            )
            identities.append((identity, principal, key))
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("性能测量 HTTP 服务启动失败")
                    await asyncio.sleep(0.01)
            url = f"http://127.0.0.1:{sock.getsockname()[1]}"
            async with AsyncExitStack() as clients:
                management = await clients.enter_async_context(
                    httpx.AsyncClient(base_url=url, timeout=120)
                )
                backends = []
                for identity, principal, key in identities:
                    backend = await clients.enter_async_context(
                        BusinessBackendClient(
                            url,
                            identity.key.api_key,
                            key.key.kid,
                            base64.b64decode(key.signing_secret),
                            "performance.example",
                            "creativity-api",
                            Principals(principal),
                        )
                    )
                    # Token 交换单独预热；每次受理仍完整执行 Token、委托和当前主体复核。
                    await backend.access_token()
                    # 采样超时与验收阈值分开，保留慢请求实际时延，不把其静默丢弃。
                    backend.http.timeout = httpx.Timeout(120)
                    backends.append(backend)
                yield SimpleNamespace(management=management, backends=backends)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
