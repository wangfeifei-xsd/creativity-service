"""MCP 标准输入输出在固定隔离容器内运行，帧和总输出均有上限。"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import anyio
from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import InitializeResult, JSONRPCMessage

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.integrations.sandbox import ContainerSandbox, SandboxProfile
from creativity_service.modules.mcp.schemas import McpTimeouts


def profile_for(sandbox: ContainerSandbox, scope: Scope, endpoint: str) -> SandboxProfile:
    parts = endpoint.removeprefix("sandbox://").split("/")
    if not endpoint.startswith("sandbox://") or len(parts) != 2:
        raise ServiceError("MCP_PROFILE_INVALID", "stdio 须选择带固定摘要的隔离环境", 422)
    profile = sandbox.profile(scope.channel_id, parts[0], "stdio")
    if digest(profile.model_dump(mode="json")) != parts[1]:
        raise ServiceError("MCP_PROFILE_CHANGED", "隔离环境已变化，请重新验证连接", 409)
    return profile


class StdioBounds:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.failure: ServiceError | None = None


async def execute_stdio[T](
    sandbox: ContainerSandbox,
    scope: Scope,
    endpoint: str,
    token: str | None,
    timeouts: McpTimeouts,
    operation: Callable[[ClientSession, InitializeResult, Any], Awaitable[T]],
) -> T:
    profile = profile_for(sandbox, scope, endpoint)
    if token and not profile.credential_env:
        raise ServiceError("MCP_PROFILE_INVALID", "此隔离环境未声明凭据注入变量", 422)
    bounds = StdioBounds(profile.output_bytes)
    read_send, read = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write, write_read = anyio.create_memory_object_stream[SessionMessage](0)
    async with sandbox.process(profile, token=token) as process:
        assert process.stdin and process.stdout and process.stderr

        async def receive() -> None:
            assert process.stdout
            total = 0
            try:
                async with read_send:
                    while line := await process.stdout.readline():
                        total += len(line)
                        if len(line) > bounds.maximum or total > profile.output_bytes:
                            raise ServiceError("MCP_RESULT_TOO_LARGE", "stdio 输出超过上限", 502)
                        await read_send.send(
                            SessionMessage(JSONRPCMessage.model_validate_json(line))
                        )
                raise ServiceError("MCP_UNAVAILABLE", "stdio 连接已关闭", 502)
            except Exception as exc:
                bounds.failure = (
                    exc
                    if isinstance(exc, ServiceError)
                    else ServiceError("MCP_RESULT_INVALID", "stdio 响应中断、无效或超过上限", 502)
                )
                raise bounds.failure from None

        async def send() -> None:
            assert process.stdin
            total = 0
            async with write_read:
                async for message in write_read:
                    data = (
                        message.message.model_dump_json(by_alias=True, exclude_none=True) + "\n"
                    ).encode()
                    total += len(data)
                    if total > profile.input_bytes:
                        raise ServiceError("MCP_INPUT_TOO_LARGE", "stdio 输入超过上限", 422)
                    process.stdin.write(data)
                    await process.stdin.drain()

        async def invoke() -> T:
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=timeouts.operation_seconds)
            ) as session:
                handshake = await session.initialize()
                return await operation(session, handshake, bounds)

        foreground = asyncio.create_task(invoke())
        tasks: list[asyncio.Task[Any]] = [
            foreground,
            asyncio.create_task(receive()),
            asyncio.create_task(send()),
            asyncio.create_task(sandbox.read(process.stderr, profile.output_bytes)),
        ]
        try:
            async with asyncio.timeout(min(profile.seconds, timeouts.operation_seconds)):
                pending = set(tasks)
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        if failure := task.exception():
                            raise failure
                    if foreground in done:
                        return foreground.result()
                raise ServiceError("MCP_UNAVAILABLE", "stdio 连接已结束", 502)
        except Exception as exc:

            def known(error: BaseException) -> ServiceError | None:
                if isinstance(error, ServiceError):
                    return error
                if isinstance(error, BaseExceptionGroup):
                    for child in error.exceptions:
                        if found := known(child):
                            return found
                return None

            raise (
                bounds.failure
                or known(exc)
                or ServiceError("MCP_UNAVAILABLE", "stdio 隔离连接失败", 502)
            ) from None
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await read.aclose()
            await write.aclose()
