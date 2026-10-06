"""锁定 SDK 的受控传输：固定地址、有界结果、独立会话且不重放业务请求。"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpcore
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, InitializeResult

from creativity_service.core.context import Scope
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError, canonical_json, digest
from creativity_service.core.security.outbound import OutboundPolicy, ValidatedTarget
from creativity_service.integrations.tools.http import PinnedBackend
from creativity_service.modules.mcp.schemas import McpTimeouts, RemoteTool
from creativity_service.modules.tools.validation import validate_schema


@dataclass(frozen=True)
class SessionKey:
    channel_id: str
    environment: str
    connection_id: str
    configuration_revision: int
    credential_ref: str | None
    credential_revision: int | None


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response, transport: "GuardedTransport") -> None:
        self.response, self.transport = response, transport

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.response.aiter_stream():
            size += len(chunk)
            if size > self.transport.maximum:
                self.transport.fail("MCP_RESULT_TOO_LARGE", "远端响应超过体积上限")
            yield chunk

    async def aclose(self) -> None:
        await self.response.aclose()


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, target: ValidatedTarget, maximum: int) -> None:
        self.target, self.maximum = target, maximum
        self.failure: ServiceError | None = None
        self.pool = httpcore.AsyncConnectionPool(network_backend=PinnedBackend(target), retries=0)

    def fail(self, code: str, message: str) -> None:
        self.failure = ServiceError(code, message, 502)
        raise self.failure

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) != self.target.url:
            self.fail("MCP_DESTINATION_FORBIDDEN", "远端请求地址发生变化")
        # 本实现不订阅服务端推送，也不恢复断开的响应流；每次调用前重新发现。
        if request.method == "GET":
            return httpx.Response(405, request=request)
        allowed = {
            "host",
            "accept",
            "content-type",
            "content-length",
            "accept-encoding",
            "user-agent",
            "connection",
            "authorization",
            "mcp-session-id",
            "mcp-protocol-version",
        }
        if any(name.lower() not in allowed for name in request.headers):
            self.fail("MCP_DESTINATION_FORBIDDEN", "远端请求头未获授权")
        response = await self.pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=str(request.url),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        headers = httpx.Headers(response.headers)
        try:
            if 300 <= response.status < 400:
                self.fail("MCP_DESTINATION_FORBIDDEN", "连接不允许重定向，请登记最终服务地址")
            if response.status in {401, 403}:
                self.fail("MCP_AUTH_FAILED", "远端鉴权失败，请更新连接凭据")
            if response.status >= 400 and not (
                request.method == "DELETE" and response.status == 405
            ):
                self.fail("MCP_UNAVAILABLE", "远端服务拒绝请求")
            if headers.get("content-encoding", "identity") not in {"", "identity"}:
                self.fail("MCP_RESULT_INVALID", "远端响应必须使用未压缩内容")
            size = headers.get("content-length")
            if size and (not size.isdigit() or int(size) > self.maximum):
                self.fail("MCP_RESULT_TOO_LARGE", "远端响应超过体积上限")
        except ServiceError:
            await response.aclose()
            raise
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=BoundedStream(response, self),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.pool.aclose()


@dataclass(frozen=True)
class DiscoveryResult:
    handshake: InitializeResult
    tools: list[RemoteTool]


def remote_tool(value: Any) -> RemoteTool:
    validate_schema(value.inputSchema)
    if value.outputSchema is not None:
        validate_schema(value.outputSchema)
    annotations = (
        value.annotations.model_dump(mode="json", exclude_none=True) if value.annotations else {}
    )
    contract = {
        "input": value.inputSchema,
        "output": value.outputSchema,
        "annotations": {k: v for k, v in annotations.items() if k != "title"},
    }
    purpose = (value.meta or {}).get("creativity/purpose", "business")
    if purpose in {"subject_review"}:
        contract["purpose"] = purpose
    return RemoteTool(
        name=value.name,
        title=value.title or annotations.get("title"),
        description=value.description or "",
        input_schema=value.inputSchema,
        output_schema=value.outputSchema,
        annotations=annotations,
        schema_hash=digest(contract),
        purpose=purpose if purpose in {"subject_review"} else "business",
    )


class McpTransport:
    """每次操作拥有自己的池和会话；无跨操作缓存，旧凭据永不复用。"""

    def __init__(self, policy: OutboundPolicy) -> None:
        self.policy = policy
        self.active: dict[SessionKey, int] = {}

    async def execute[T](
        self,
        scope: Scope,
        key: SessionKey,
        endpoint: str,
        token: str | None,
        timeouts: McpTimeouts,
        operation: Callable[[ClientSession, InitializeResult, GuardedTransport], Awaitable[T]],
    ) -> T:
        assert_external_io_allowed()
        if endpoint.startswith("sandbox://"):
            from creativity_service.integrations.sandbox import ContainerSandbox
            from creativity_service.modules.mcp.stdio import execute_stdio

            self.active[key] = self.active.get(key, 0) + 1
            try:
                return await execute_stdio(
                    ContainerSandbox(), scope, endpoint, token, timeouts, operation
                )
            finally:
                self.active[key] -= 1
                if self.active[key] == 0:
                    self.active.pop(key)
        target = await self.policy.validate(scope, "mcp", endpoint)
        transport = GuardedTransport(target, 2 * 1024 * 1024)
        headers = {"Accept-Encoding": "identity"}
        if token is not None:
            if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ServiceError("MCP_AUTH_FAILED", "服务令牌格式不正确", 422)
            headers["Authorization"] = f"Bearer {token}"
        self.active[key] = self.active.get(key, 0) + 1
        try:
            async with asyncio.timeout(timeouts.operation_seconds):
                async with httpx.AsyncClient(
                    transport=transport,
                    headers=headers,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=httpx.Timeout(
                        timeouts.operation_seconds, connect=timeouts.connect_seconds
                    ),
                ) as client:
                    async with streamable_http_client(endpoint, http_client=client) as (
                        read,
                        write,
                        _,
                    ):
                        async with ClientSession(
                            read,
                            write,
                            read_timeout_seconds=timedelta(seconds=timeouts.operation_seconds),
                        ) as session:
                            handshake = await session.initialize()
                            return await operation(session, handshake, transport)
        except Exception as exc:
            if transport.failure:
                raise transport.failure from None

            # SDK 任务组可能包裹异常，绝不把远端文本、地址或认证头写入错误。
            def known(error: BaseException) -> ServiceError | None:
                if isinstance(error, ServiceError):
                    return error
                if isinstance(error, RuntimeError) and "Unsupported protocol version" in str(error):
                    return ServiceError("MCP_PROTOCOL_MISMATCH", "远端协议版本不兼容", 502)
                if isinstance(error, RuntimeError) and any(
                    part in str(error)
                    for part in (
                        "output schema",
                        "Invalid structured content",
                        "Invalid schema for tool",
                    )
                ):
                    return ServiceError("MCP_RESULT_INVALID", "远端工具结果不符合契约", 502)
                if isinstance(error, BaseExceptionGroup):
                    for child in error.exceptions:
                        found = known(child)
                        if found:
                            return found
                return None

            safe = known(exc)
            if safe:
                raise safe from None
            if isinstance(exc, TimeoutError):
                raise ServiceError("MCP_UNAVAILABLE", "远端响应超时，调用结果待核实", 504) from None
            if "Unsupported protocol version" in str(exc):
                raise ServiceError("MCP_PROTOCOL_MISMATCH", "远端协议版本不兼容", 502) from None
            raise ServiceError("MCP_UNAVAILABLE", "远端连接中断或协议响应无效", 502) from None
        finally:
            self.active[key] -= 1
            if self.active[key] == 0:
                self.active.pop(key)

    @staticmethod
    async def list_tools(session: ClientSession, handshake: InitializeResult) -> list[RemoteTool]:
        if handshake.capabilities.tools is None:
            raise ServiceError("MCP_PROTOCOL_MISMATCH", "远端未协商工具能力", 502)
        tools: list[RemoteTool] = []
        names: set[str] = set()
        cursors: set[str] = set()
        cursor = None
        for _ in range(100):
            page = await session.list_tools(cursor=cursor)
            for value in page.tools:
                tool = remote_tool(value)
                if tool.name in names:
                    raise ServiceError("MCP_PROTOCOL_MISMATCH", "远端工具名称重复", 502)
                names.add(tool.name)
                tools.append(tool)
            if (
                len(tools) > 1000
                or len(canonical_json([t.model_dump() for t in tools])) > 2 * 1024 * 1024
            ):
                raise ServiceError("MCP_RESULT_TOO_LARGE", "发现结果超过数量或体积上限", 502)
            cursor = page.nextCursor
            if cursor is None:
                return tools
            if not cursor or cursor in cursors:
                raise ServiceError("MCP_PROTOCOL_MISMATCH", "远端发现游标无效或重复", 502)
            cursors.add(cursor)
        raise ServiceError("MCP_RESULT_TOO_LARGE", "远端工具分页超过上限", 502)

    async def discover(
        self, scope: Scope, key: SessionKey, endpoint: str, token: str | None, timeouts: McpTimeouts
    ) -> DiscoveryResult:
        async def operation(
            session: ClientSession, handshake: InitializeResult, transport: GuardedTransport
        ) -> DiscoveryResult:
            tools = await self.list_tools(session, handshake)
            if (
                token
                and token
                in canonical_json(
                    {
                        "handshake": handshake.model_dump(mode="json"),
                        "tools": [t.model_dump(mode="json") for t in tools],
                    }
                ).decode()
            ):
                raise ServiceError("MCP_RESULT_INVALID", "远端响应含服务认证信息，已拒绝保存", 502)
            return DiscoveryResult(handshake, tools)

        # 仅连接测试/发现可以完整重建一次；业务调用没有此循环。
        for attempt in range(2):
            try:
                return await self.execute(scope, key, endpoint, token, timeouts, operation)
            except ServiceError as exc:
                if exc.code != "MCP_UNAVAILABLE" or attempt:
                    raise
        raise AssertionError("连接重建分支不应抵达末尾")

    async def call(
        self,
        scope: Scope,
        key: SessionKey,
        endpoint: str,
        token: str | None,
        timeouts: McpTimeouts,
        remote_name: str,
        schema_hash: str,
        arguments: dict[str, Any],
        maximum: int,
        before_send: Callable[[], Awaitable[None]],
        identity: dict[str, Any] | None = None,
    ) -> CallToolResult:
        submitted = False

        async def operation(
            session: ClientSession, handshake: InitializeResult, transport: GuardedTransport
        ) -> CallToolResult:
            nonlocal submitted
            tools = await self.list_tools(session, handshake)
            current = next((t for t in tools if t.name == remote_name), None)
            if current is None or current.schema_hash != schema_hash:
                raise ServiceError(
                    "MCP_TOOL_CHANGED", "远端工具契约已变更，请导入新版本并重新验证", 409
                )
            await before_send()
            transport.maximum = maximum
            submitted = True
            result = await session.call_tool(
                remote_name, arguments, meta={"creativity.identity": identity} if identity else None
            )
            if token and token in result.model_dump_json():
                raise ServiceError("MCP_RESULT_INVALID", "远端响应含服务认证信息，已拒绝交付", 502)
            return result

        try:
            return await self.execute(scope, key, endpoint, token, timeouts, operation)
        except ServiceError as exc:
            if submitted and exc.code == "MCP_UNAVAILABLE":
                raise ServiceError(
                    "MCP_RESULT_UNKNOWN", "远端调用已提交，结果待核实", 502
                ) from None
            raise
