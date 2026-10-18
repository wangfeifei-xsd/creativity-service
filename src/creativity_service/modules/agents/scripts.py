"""节点脚本的静态检查和渠道执行环境解析，不在管理进程执行源码。"""

import ast

from creativity_service.core.primitives import ServiceError, digest
from creativity_service.integrations.sandbox import ContainerSandbox, SandboxProfile
from creativity_service.modules.agents.schemas import (
    AgentDefinition,
    AgentIssue,
    AgentStep,
    NodePython,
    PythonEnvironment,
)


def check_source(source: str) -> None:
    try:
        tree = ast.parse(source, filename="节点脚本.py")
        compile(tree, "节点脚本.py", "exec")
    except (SyntaxError, ValueError, RecursionError) as exc:
        line = getattr(exc, "lineno", None)
        raise ServiceError(
            "NODE_SCRIPT_INVALID", f"Python 脚本语法错误（第 {line or 1} 行）", 422
        ) from None
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "transform"]
    if len(functions) != 1:
        raise ServiceError("NODE_SCRIPT_INVALID", "须定义 transform(value, context) 函数", 422)
    args = functions[0].args
    if (
        len(args.posonlyargs + args.args) != 2
        or args.vararg
        or args.kwarg
        or args.kwonlyargs
        or functions[0].decorator_list
    ):
        raise ServiceError("NODE_SCRIPT_INVALID", "transform 须接收两个参数且不能使用装饰器", 422)


def script_configuration_issues(step: AgentStep) -> list[AgentIssue]:
    issues = []

    def fail(message: str, field: str = "script", code: str = "FLOW_INVALID") -> None:
        issues.append(AgentIssue(code=code, message=message, path=f"steps.{step.key}.{field}"))

    if step.kind == "script":
        if step.script is None:
            fail("脚本处理节点须填写 Python 脚本")
        if step.dependency or step.prompt_id or step.operator or step.failure_handler:
            fail("脚本处理节点不能配置模型、提示词、工具、算子或失败收尾工具")
        if step.max_retries or step.failure_policy == "retry":
            fail("脚本处理异常不自动重试，请通过业务输出和连线处理校验失败", "failure_policy")
        if step.pagination:
            fail("脚本处理节点不能配置工具分页", "pagination")
    elif step.script is not None:
        fail("只有脚本处理节点可以配置 Python 脚本")
    if step.script:
        try:
            check_source(step.script.source)
        except ServiceError as exc:
            fail(exc.message, code=exc.code)
    return issues


def script_key(node_key: str) -> str:
    return f"python_{digest(node_key)[:32]}"


def resolve_profile(
    sandbox: ContainerSandbox, channel_id: str, script: NodePython
) -> SandboxProfile:
    profile = sandbox.profile(channel_id, script.profile_id, "python")
    if profile.credential_env or profile.command not in {
        ("python3", "-I", "-B"),
        ("python3", "-I", "-B", "/work/main.py"),
    }:
        raise ServiceError("SANDBOX_PROFILE_INVALID", "节点脚本须使用无凭据的 Python 隔离环境", 422)
    if digest(profile.model_dump(mode="json")) != script.profile_digest:
        raise ServiceError("SANDBOX_PROFILE_CHANGED", "Python 执行环境已变化，请重新选择", 409)
    return profile


def environment_options(sandbox: ContainerSandbox, channel_id: str) -> list[PythonEnvironment]:
    result = []
    for profile in sandbox.settings.profiles:
        if profile.mode != "python" or channel_id not in profile.channels:
            continue
        fingerprint = digest(profile.model_dump(mode="json"))
        try:
            resolve_profile(
                sandbox,
                channel_id,
                NodePython(
                    source="候选环境", profile_id=profile.profile_id, profile_digest=fingerprint
                ),
            )
        except ServiceError:
            continue
        result.append(
            PythonEnvironment(
                profile_id=profile.profile_id,
                profile_digest=fingerprint,
                name=profile.name,
                seconds=profile.seconds,
                memory_mb=profile.memory_mb,
            )
        )
    return result


def script_issues(
    definition: AgentDefinition,
    sandbox: ContainerSandbox,
    channel_id: str,
    *,
    check_config: bool = True,
) -> list[AgentIssue]:
    issues = []
    for step in definition.steps:
        if check_config:
            issues.extend(script_configuration_issues(step))
        if step.script is None:
            continue
        try:
            resolve_profile(sandbox, channel_id, step.script)
        except ServiceError as exc:
            issues.append(
                AgentIssue(code=exc.code, message=exc.message, path=f"steps.{step.key}.script")
            )
    return issues
