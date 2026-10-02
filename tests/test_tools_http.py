"""受控本地 HTTP 夹具验证地址钉住、大小上限与安全错误映射。"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.tools import AdapterRequest, ToolAdapterError
from creativity_service.integrations.tools.http import HttpToolAdapter
from creativity_service.modules.tools.schemas import ToolDefinition


@pytest.fixture
def source():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(
                {
                    "data": {"value": "x" * 2048 if self.path == "/large" else "价格"},
                    "source_request_id": "fixture-source",
                    "source_version": "1",
                    "observed_at": utcnow().isoformat(),
                }
            ).encode()
            self.send_response(503 if self.path == "/unavailable" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-ID", "fixture-source")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, calls
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request():
    context = AuthContext(
        scope=Scope(channel_id="channel_a", environment="test", data_scope_id="club_a"),
        principal_type="management",
        principal_id="admin_a",
        actor_id="admin_a",
        request_id="request_a",
    )
    definition = ToolDefinition(
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        model_fields_allowed=("query",),
        binding={"adapter_key": "http_fixture", "implementation_version": "1"},
        effect_type="READ_ONLY",
        allowed_data_domains=("club_a",),
        environments=("test",),
        max_result_size=1024,
    )
    return AdapterRequest(context, {"query": "价格"}, "attempt_a", definition)


def adapter(port, path):
    async def resolve(hostname, resolved_port):
        assert hostname == "does-not-resolve.invalid" and resolved_port == port
        return ["127.0.0.1"]

    policy = OutboundPolicy(
        (
            Destination(
                "channel_a",
                "test",
                "http_tool",
                "does-not-resolve.invalid",
                port=port,
                scheme="http",
                allowed_networks=("127.0.0.1/32",),
            ),
        ),
        resolve,
    )
    return HttpToolAdapter(f"http://does-not-resolve.invalid:{port}{path}", "POST", policy)


async def test_http_pins_approved_address_and_injects_verified_identity(source):
    port, calls = source
    result = await adapter(port, "/query").invoke(request())
    assert result.data == {"value": "价格"}
    assert calls == [
        {
            "arguments": {"query": "价格"},
            "identity": {
                "channel_id": "channel_a",
                "environment": "test",
                "data_scope_id": "club_a",
                "subject_type": None,
                "subject_id": None,
                "principal_id": "admin_a",
                "client_id": None,
                "key_id": None,
            },
        }
    ]


@pytest.mark.parametrize(
    ("path", "code", "retryable"),
    [
        ("/large", "TOOL_RESULT_INVALID", False),
        ("/redirect", "TOOL_UNAVAILABLE", False),
        ("/unavailable", "TOOL_UNAVAILABLE", True),
    ],
)
async def test_http_size_redirect_and_retry_mapping(source, path, code, retryable):
    port, calls = source
    with pytest.raises(ToolAdapterError) as error:
        await adapter(port, path).invoke(request())
    assert error.value.code == code and error.value.retryable is retryable
    assert len(calls) == 1


async def test_http_unregistered_host_never_reaches_source(source):
    port, calls = source
    tool = HttpToolAdapter(f"http://127.0.0.1:{port}/query", "GET", OutboundPolicy(()))
    with pytest.raises(ServiceError) as error:
        await tool.invoke(request())
    assert error.value.code == "DESTINATION_FORBIDDEN" and not calls


async def test_http_timeout_closes_inflight_connection():
    disconnected = asyncio.Event()

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n"
            )
            await writer.drain()
            await reader.read()
            disconnected.set()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        item = request()
        item = type(item)(
            item.context,
            item.arguments,
            item.attempt_id,
            item.definition.model_copy(update={"timeout_seconds": 1}),
        )
        with pytest.raises(ToolAdapterError) as error:
            await adapter(server.sockets[0].getsockname()[1], "/query").invoke(item)
        assert error.value.code == "TOOL_TIMEOUT"
        await asyncio.wait_for(disconnected.wait(), 1)
    finally:
        server.close()
        await server.wait_closed()
