"""性能测量的真实 HTTP 与四渠道凭据装配；仅使用隔离测试种子。"""

import asyncio
import socket
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace

import httpx
import uvicorn

from examples.backend.client import BusinessBackendClient, Principal
from tests.integration.channels.conftest import credential


class Principals:
    def __init__(self, value):
        self.value = value

    async def current(self):
        return self.value


@asynccontextmanager
async def http_clients(env, tenants):
    app = env.client._transport.app
    with socket.socket() as sock:
        identities = []
        for tenant in tenants:
            identity = await credential(env, tenant, "性能测量后端")
            identities.append((identity, Principal("member", "same-performance-user")))
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
                for identity, principal in identities:
                    backend = await clients.enter_async_context(
                        BusinessBackendClient(
                            url,
                            identity.key.key.key_id,
                            identity.key.api_key,
                            Principals(principal),
                        )
                    )
                    # Token 交换单独预热；每次受理仍完整执行 渠道 Token 与当前凭据校验。
                    await backend.access_token()
                    # 采样超时与验收阈值分开，保留慢请求实际时延，不把其静默丢弃。
                    backend.http.timeout = httpx.Timeout(120)
                    backends.append(backend)
                yield SimpleNamespace(management=management, backends=backends)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
