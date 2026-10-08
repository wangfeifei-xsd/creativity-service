"""使用真实 TCP HTTP 服务模拟受控 MCP 协议与故障，绝不访问生产数据。"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import uuid4

import pytest


@pytest.fixture
def mcp_source():
    state = SimpleNamespace(
        calls=[],
        sessions={},
        mode="normal",
        token=None,
        schema_revision=1,
        app_id="isolated-app",
        app_secret="isolated-app-secret-only",
        token_exchanges=0,
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def answer(self, status, body=None, headers=None):
            content = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if content:
                self.wfile.write(content)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/token":
                form = parse_qs(body.decode())
                state.token_exchanges += 1
                if form != {
                    "grant_type": ["client_credentials"],
                    "client_id": [state.app_id],
                    "client_secret": [state.app_secret],
                    "resource": [state.endpoint],
                    "scope": ["mcp"],
                }:
                    self.answer(401, {"error": "invalid_client"})
                    return
                state.token = uuid4().hex
                self.answer(
                    200,
                    {
                        "access_token": state.token,
                        "token_type": "Bearer",
                        "expires_in": 900,
                        "scope": "mcp",
                    },
                )
                return
            message = json.loads(body)
            state.calls.append(
                (message, self.headers.get("Authorization"), self.headers.get("Mcp-Session-Id"))
            )
            if state.mode == "auth" or (
                state.token and self.headers.get("Authorization") != f"Bearer {state.token}"
            ):
                self.answer(401)
                return
            if state.mode == "redirect":
                self.answer(307, headers={"Location": "/other"})
                return
            method = message["method"]
            if "id" not in message:
                self.answer(202)
                return
            headers = {}
            if method == "initialize":
                session = uuid4().hex
                state.sessions[session] = self.headers.get("Authorization")
                headers = {"Mcp-Session-Id": session}
                result = {
                    "protocolVersion": "2099-01-01" if state.mode == "protocol" else "2025-11-25",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "受控工具服务", "version": "1.0"},
                }
            elif method == "tools/list":
                cursor = message.get("params", {}).get("cursor")
                if cursor and state.mode != "cursor":
                    result = {"tools": []}
                else:
                    properties = {"query": {"type": "string"}}
                    if state.schema_revision > 1:
                        properties["limit"] = {"type": "integer"}
                    result = {
                        "tools": [
                            {
                                "name": "lookup",
                                "title": "目录查询",
                                "description": "查询授权范围中的目录",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": properties,
                                    "required": list(properties),
                                },
                                "outputSchema": {
                                    "type": "object",
                                    "properties": {"value": {"type": "string"}},
                                    "required": ["value"],
                                },
                                "annotations": {"readOnlyHint": True},
                            }
                        ],
                        "nextCursor": "second",
                    }
            elif method == "tools/call":
                if state.mode == "disconnect":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if state.mode == "file":
                    result = {
                        "content": [
                            {
                                "type": "resource_link",
                                "uri": "file:///private/secret",
                                "name": "越权文件",
                            }
                        ],
                        "structuredContent": {"value": "可用"},
                    }
                else:
                    value = "x" * 10000 if state.mode == "large" else "可用"
                    result = {
                        "content": [{"type": "text", "text": value}],
                        "structuredContent": {"value": value},
                        "isError": False,
                    }
            else:
                result = {}
            self.answer(200, {"jsonrpc": "2.0", "id": message["id"], "result": result}, headers)

        def do_DELETE(self):
            self.answer(200)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.port = server.server_port
    state.endpoint = f"http://127.0.0.1:{server.server_port}/mcp"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
