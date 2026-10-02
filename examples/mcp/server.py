"""受控测试 MCP 服务：两套不同工具契约及可注入故障，不代表正式业务实现。"""

import argparse
import json
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

from creativity_service.modules.integrations.subject_contracts import (
    SubjectReviewRequest,
    SubjectReviewResponse,
)


@contextmanager
def serve(profile="archive", port=0, token="fixture-service-only"):
    state = SimpleNamespace(
        profile=profile,
        token=token,
        scope=None,
        permissions={},
        calls=[],
        mode="normal",
        review_mode="normal",
        review_delay=0,
        schema_revision=1,
        actual_effect="READ_ONLY",
    )
    state.tool_name = "archive.find-notes" if profile == "archive" else "matrix.total"
    state.title = "档案摘录" if profile == "archive" else "矩阵合计"
    state.arguments = (
        {"document": "note-a"} if profile == "archive" else {"matrix": [[1, 2], [3, 4]]}
    )
    state.data = (
        {"notes": [{"heading": "受控资料", "text": "已授权内容"}]}
        if profile == "archive"
        else {"total": 10, "unit": "分"}
    )
    state.input_schema = (
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"document": {"type": "string", "title": "文档"}},
            "required": ["document"],
        }
        if profile == "archive"
        else {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "matrix": {"type": "array", "title": "矩阵", "items": {"$ref": "#/$defs/row"}}
            },
            "required": ["matrix"],
            "$defs": {"row": {"type": "array", "items": {"type": "number"}}},
        }
    )
    state.output_schema = (
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "notes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"heading": {"type": "string"}, "text": {"type": "string"}},
                        "required": ["heading", "text"],
                    },
                }
            },
            "required": ["notes"],
        }
        if profile == "archive"
        else {
            "type": "object",
            "additionalProperties": False,
            "properties": {"total": {"type": "number"}, "unit": {"type": "string"}},
            "required": ["total", "unit"],
        }
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def answer(self, status, body=None):
            value = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            try:
                self.wfile.write(value)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.headers.get("Authorization") != f"Bearer {state.token}":
                self.answer(401)
                return
            method = message["method"]
            if "id" not in message:
                self.answer(202)
                return
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": f"受控测试服务：{state.title}",
                        "version": "fixture-20-v1",
                    },
                }
            elif method == "tools/list":
                schema = json.loads(json.dumps(state.input_schema))
                if state.schema_revision != 1:
                    schema["properties"]["revision_hint"] = {"type": "string"}
                    schema["required"].append("revision_hint")
                result = {
                    "tools": [
                        {
                            "name": state.tool_name,
                            "title": state.title,
                            "description": "查询受信范围中的受控测试数据",
                            "inputSchema": schema,
                            "outputSchema": state.output_schema,
                            "annotations": {"readOnlyHint": True},
                        },
                        {
                            "name": "access.review-current",
                            "title": "当前主体权限",
                            "description": "按服务身份查询当前主体",
                            "inputSchema": SubjectReviewRequest.model_json_schema(),
                            "outputSchema": SubjectReviewResponse.model_json_schema(),
                            "annotations": {"readOnlyHint": True},
                            "_meta": {"creativity/purpose": "subject_review"},
                        },
                    ]
                }
            elif method == "tools/call":
                params = message["params"]
                state.calls.append(params)
                result = (
                    self.review(params["arguments"])
                    if params["name"] == "access.review-current"
                    else self.business(params)
                )
            else:
                result = {}
            self.answer(200, {"jsonrpc": "2.0", "id": message["id"], "result": result})

        def review(self, arguments):
            if state.review_delay:
                time.sleep(state.review_delay)
            now = datetime.now(UTC)
            scope = arguments["scope"]
            permission = state.permissions.get(scope.get("subject_id"))
            actions = (
                set(permission["actions"]) & set(arguments["actions"]) if permission else set()
            )
            resources = {}
            if permission:
                for kind, ceiling in arguments["resources"].items():
                    current = set(permission["resources"].get(kind, []))
                    resources[kind] = sorted(
                        current
                        if "*" in ceiling
                        else set(ceiling)
                        if "*" in current
                        else current & set(ceiling)
                    )
            active = scope == state.scope and bool(permission) and state.review_mode != "revoked"
            if state.review_mode == "widen":
                actions.add("memory:write")
            if state.review_mode == "wrong_scope":
                scope = {**scope, "data_scope_id": "foreign"}
            if state.review_mode == "expired":
                now -= timedelta(minutes=1)
            result = {
                "protocol": "creativity.subject-review.v1",
                "scope": scope,
                "active": active,
                "actions": sorted(actions),
                "agent_actions": sorted(actions),
                "resources": resources,
                "observed_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=20)).isoformat(),
            }
            return {
                "content": [],
                "structuredContent": result,
                "isError": state.review_mode == "error",
            }

        def business(self, params):
            identity = params.get("_meta", {}).get("creativity.identity", {})
            scope = {
                key: identity.get(key)
                for key in (
                    "channel_id",
                    "environment",
                    "data_scope_id",
                    "subject_type",
                    "subject_id",
                )
            }
            permission = state.permissions.get(scope.get("subject_id"))
            now = datetime.now(UTC)
            denied = (
                scope != state.scope
                or not permission
                or "run:create" not in permission["actions"]
                or identity.get("protocol") != "creativity.mcp-identity.v1"
                or datetime.fromisoformat(identity.get("expires_at", "2000-01-01T00:00:00+00:00"))
                <= now
            )
            if state.profile == "archive" and params["arguments"].get("document") != "note-a":
                denied = True
            meta = {
                "protocol": "creativity.tool-result.v1",
                "scope": scope,
                "source_request_id": "fixture-source-" + uuid4().hex,
                "source_version": "fixture-data-v1",
                "observed_at": now.isoformat(),
                "actual_effect": state.actual_effect,
                "result_status": "complete",
                "coverage": {"verification": "controlled_fixture"},
                "warnings": [],
            }
            if denied or state.mode in {"forbidden", "timeout", "error"}:
                meta["error_category"] = (
                    "forbidden" if denied else {"error": "remote"}.get(state.mode, state.mode)
                )
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": "源端拒绝或失败"}],
                    "_meta": {"creativity.result": meta},
                }
            data = json.loads(json.dumps(state.data))
            if state.profile == "matrix":
                data["total"] = sum(sum(row) for row in params["arguments"]["matrix"])
            if state.mode in {"empty", "missing"}:
                meta["result_status"] = state.mode
                if state.profile == "archive":
                    data = {"notes": []}
                meta["warnings"] = ["源端无记录" if state.mode == "empty" else "源字段缺失"]
            if state.mode == "partial":
                meta.update(
                    result_status="partial",
                    has_more=True,
                    cursor="next-page",
                    coverage={"returned_count": 1, "verification": "controlled_fixture"},
                )
            if state.mode == "wrong_scope":
                meta["scope"] = {**scope, "data_scope_id": "foreign"}
            if state.mode == "bad_schema":
                data = {"unknown": True}
            meta["evidence"] = [
                {
                    "scope": meta["scope"],
                    "source_id": "source/document/note-a",
                    "title": "受控源资料",
                    "source_version": "fixture-data-v1",
                    "observed_at": now.isoformat(),
                    "location": {
                        "field_path": ["notes"] if state.profile == "archive" else ["total"],
                        "text_start": None,
                        "text_end": None,
                    },
                }
            ]
            return {"content": [], "structuredContent": data, "_meta": {"creativity.result": meta}}

        def do_GET(self):
            self.answer(405)

        def do_DELETE(self):
            self.answer(200)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.port, state.endpoint = server.server_port, f"http://127.0.0.1:{server.server_port}/mcp"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description="启动受控测试 MCP 服务")
    parser.add_argument("--profile", choices=["archive", "matrix"], required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--identity-file", required=True)
    args = parser.parse_args()
    with open(args.identity_file) as source:
        identity = json.load(source)
    with serve(args.profile, args.port) as state:
        state.scope, state.permissions = identity["scope"], identity["permissions"]
        print(f"受控测试服务已启动：{state.endpoint}", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
