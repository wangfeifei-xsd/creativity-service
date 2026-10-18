"""脚本节点使用隔离 Python，处理事实与恢复共用运行步骤。"""

from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.core.primitives import ServiceError, digest
from creativity_service.integrations.sandbox import ContainerSandbox
from creativity_service.modules.agents.schemas import NodePython
from creativity_service.modules.agents.scripts import resolve_profile, script_key
from creativity_service.modules.runs.schemas import Lease
from creativity_service.modules.runs.services import RunService

BOOTSTRAP = b"""import sys, json, contextlib
payload = json.load(sys.stdin)
try:
    with contextlib.redirect_stdout(sys.stderr):
        namespace = {"__name__": "node_script"}
        exec(compile(payload["source"], "node_script.py", "exec"), namespace)
        result = namespace["transform"](payload["value"], payload["context"])
    if not isinstance(result, dict):
        raise ValueError("transform must return a JSON object")
    encoded = json.dumps({"ok": True, "value": result}, ensure_ascii=False, allow_nan=False)
except Exception as error:
    message = type(error).__name__ + ": " + str(error)[:1000]
    encoded = json.dumps({"ok": False, "error": message}, ensure_ascii=False)
print(encoded)
"""


class NodeScripts:
    def __init__(self, runs: RunService, sandbox: ContainerSandbox) -> None:
        self.runs, self.sandbox = runs, sandbox

    async def execute(
        self,
        lease: Lease,
        node: str,
        script: NodePython,
        value: dict[str, Any],
        context: dict[str, Any],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        key = script_key(node)
        label = "脚本处理"
        # 恢复也复核渠道授权和环境摘要，不能因已有结果绕过撤权。
        profile = resolve_profile(self.sandbox, lease.scope.channel_id, script)
        saved = await self.runs.load_progress(lease, key)
        if saved and saved["output"] is not None:
            await self.runs.commit_step(lease, key, saved["output"])
            return dict(saved["output"])
        payload = {"value": value, "context": context, "source_digest": digest(script.source)}
        if await self.runs.start_step(lease, key, payload) is None:
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
        attempt = await self.runs.start_attempt(lease, key)
        if attempt is None:
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
        failure_content: dict[str, Any] | None = None
        try:
            response = await self.sandbox.python(
                profile, BOOTSTRAP, {**payload, "source": script.source}
            )
            if not isinstance(response, dict) or response.get("ok") is not True:
                message = (
                    str(response.get("error", "脚本未返回有效结果"))[:1100]
                    if isinstance(response, dict)
                    else "脚本未返回有效结果"
                )
                failure_content = {"script_error": message}
                raise ServiceError("NODE_SCRIPT_FAILED", f"{label}失败，请查看授权的处理记录", 422)
            output = response.get("value")
            if not isinstance(output, dict) or not Draft202012Validator(schema).is_valid(output):
                failure_content = {"invalid_result": output}
                raise ServiceError("NODE_SCRIPT_RESULT_INVALID", f"{label}结果未通过结构校验", 422)
        except (ServiceError, OSError) as exc:
            failure = (
                exc
                if isinstance(exc, ServiceError)
                else ServiceError("SANDBOX_UNAVAILABLE", "Python 隔离执行服务不可用", 503)
            )
            row = await self.runs.before_progress(lease)
            await self.runs.finish_attempt(
                lease,
                attempt["id"],
                "FAILED",
                retryable=False,
                output=failure_content,
                error=self.runs.error(row, failure.code, failure.message),
            )
            raise failure from exc
        await self.runs.finish_attempt(lease, attempt["id"], "SUCCEEDED", output=output)
        await self.runs.commit_step(lease, key, output)
        return output
