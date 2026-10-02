"""运行与加载测试共用的确定性加载器；不解释技能文本中的授权指令。"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.skills.packages import parse_entry
from creativity_service.modules.skills.schemas import (
    SkillBinding,
    SkillDefinition,
    SkillDiscovery,
    SkillIssue,
    SkillLoadedFile,
    SkillLoadRequest,
    SkillLoadResult,
    SkillOmission,
)

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class ResolvedSkill:
    skill_id: str
    version_id: str
    revision: int
    state: str
    active: bool
    definition: SkillDefinition


class SkillLoadPort(Protocol):
    async def resolve(
        self, context: AuthContext, version_id: str, purpose: str
    ) -> ResolvedSkill: ...

    async def read_files(
        self, context: AuthContext, skill: ResolvedSkill, paths: tuple[str, ...], purpose: str
    ) -> dict[str, bytes]: ...

    async def recheck(self, context: AuthContext, skill: ResolvedSkill, purpose: str) -> None: ...


def variable_issues(skill: ResolvedSkill, binding: SkillBinding) -> list[SkillIssue]:
    issues = []
    definitions = {v.name: v for v in skill.definition.input_variables}
    if set(binding.variables) - definitions.keys():
        issues.append(SkillIssue(code="SKILL_VARIABLE_INVALID", message="存在未声明的输入变量"))
    types: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "number": (int, float),
        "boolean": (bool,),
        "object": (dict,),
        "array": (list,),
    }
    for name, variable in definitions.items():
        if name not in binding.variables:
            if variable.required:
                issues.append(
                    SkillIssue(
                        code="SKILL_VARIABLE_INVALID",
                        message=f"缺少输入变量：{variable.label}",
                        path=name,
                    )
                )
        elif type(binding.variables[name]) not in types[variable.value_type]:
            issues.append(
                SkillIssue(
                    code="SKILL_VARIABLE_INVALID",
                    message=f"输入变量类型不符：{variable.label}",
                    path=name,
                )
            )
    return issues


class SkillLoader:
    def __init__(self, port: SkillLoadPort) -> None:
        self.port = port

    async def load(self, context: AuthContext, request: SkillLoadRequest) -> SkillLoadResult:
        if len({b.version_id for b in request.bindings}) != len(request.bindings):
            raise ServiceError("SKILL_CONFLICT", "同一技能版本不能重复绑定", 422)
        resolved = []
        issues = []
        omitted: list[SkillOmission] = []
        groups: set[str] = set()
        resources: set[str] = set()
        for index, binding in enumerate(request.bindings):
            skill = await self.port.resolve(context, binding.version_id, request.purpose)
            definition = skill.definition
            active = (
                binding.loading_mode or definition.loading_mode
            ) == "mandatory" or binding.selected
            if not skill.active or request.purpose == "runtime" and skill.state != "PUBLISHED":
                issues.append(
                    SkillIssue(code="SKILL_UNAVAILABLE", message="技能已停用或版本未冻结")
                )
            if skill.skill_id in resources:
                issues.append(
                    SkillIssue(code="SKILL_CONFLICT", message="同一技能不能同时绑定多个版本")
                )
            resources.add(skill.skill_id)
            if definition.allowed_agents and request.agent_id not in definition.allowed_agents:
                issues.append(
                    SkillIssue(code="SKILL_FORBIDDEN", message="当前智能体未获准加载此技能")
                )
            if set(definition.required_tool_versions) - set(request.authorized_tool_versions):
                issues.append(
                    SkillIssue(
                        code="SKILL_DEPENDENCY_MISSING", message="技能工具不在智能体授权集合中"
                    )
                )
            if set(definition.required_model_capabilities) - set(request.model_capabilities):
                issues.append(
                    SkillIssue(code="SKILL_DEPENDENCY_MISSING", message="模型能力不满足技能要求")
                )
            if active:
                conflicts = groups.intersection(definition.conflict_groups)
                if conflicts:
                    issues.append(
                        SkillIssue(
                            code="SKILL_CONFLICT",
                            message="技能规则冲突：" + "、".join(sorted(conflicts)),
                        )
                    )
                groups.update(definition.conflict_groups)
                issues.extend(variable_issues(skill, binding))
                paths = {f.relative_path for f in definition.files}
                if set(binding.selected_files) - paths:
                    issues.append(
                        SkillIssue(code="SKILL_FORMAT_INVALID", message="所选资料不在技能包内")
                    )
            resolved.append((index, binding, skill, active))
        if issues:
            return SkillLoadResult(
                loaded=[],
                discoveries=[],
                omitted=[
                    SkillOmission(
                        version_id=s.version_id, path=f.relative_path, reason="加载校验未通过"
                    )
                    for _, _, s, _ in resolved
                    for f in s.definition.files
                ],
                issues=issues,
                used_budget=0,
                callable_tool_versions=[],
                complete=False,
            )
        # 数字越小越先加载；同一优先级遵循 Agent 绑定顺序，保持跨进程结果一致。
        resolved.sort(
            key=lambda v: (
                v[1].priority if v[1].priority is not None else v[2].definition.priority,
                v[0],
            )
        )
        loaded = []
        discoveries = []
        used = 0
        callable_tools: set[str] = set()
        for _, binding, skill, active in resolved:
            definition = skill.definition
            if not active:
                text = json.dumps(
                    {
                        "name": definition.metadata["name"],
                        "description": definition.metadata["description"],
                    },
                    ensure_ascii=False,
                )
                size = len(text.encode())
                if size > min(request.context_budget - used, definition.context_budget):
                    issues.append(
                        SkillIssue(code="SKILL_CONTEXT_LIMIT", message="技能发现信息超过上下文预算")
                    )
                else:
                    used += size
                    discoveries.append(
                        SkillDiscovery(
                            version_id=skill.version_id,
                            name=definition.metadata["name"],
                            description=definition.metadata["description"],
                            text=text,
                        )
                    )
                omitted.extend(
                    SkillOmission(
                        version_id=skill.version_id, path=f.relative_path, reason="按需技能尚未触发"
                    )
                    for f in definition.files
                )
                continue
            wanted = [
                "SKILL.md",
                *dict.fromkeys(p for p in binding.selected_files if p != "SKILL.md"),
            ]
            by_path = {f.relative_path: f for f in definition.files}
            readable = tuple(p for p in wanted if by_path[p].loadable)
            data = await self.port.read_files(context, skill, readable, request.purpose)
            skill_used = 0
            entry_loaded = False
            for path in wanted:
                file = by_path[path]
                reason = file.unavailable_reason
                if not reason:
                    content = data[path]
                    if (
                        len(content) != file.size_bytes
                        or hashlib.sha256(content).hexdigest() != file.sha256
                    ):
                        raise ServiceError("SKILL_PACKAGE_CORRUPT", "技能文件摘要校验失败", 503)
                    text = (
                        parse_entry(content)[1]
                        if path == "SKILL.md"
                        else content.decode("utf-8-sig")
                    )
                    missing = set(PLACEHOLDER.findall(text)) - binding.variables.keys()
                    if missing:
                        reason = "资料包含尚未提供的变量"
                        issues.append(
                            SkillIssue(code="SKILL_VARIABLE_INVALID", message=reason, path=path)
                        )
                    else:

                        def substitute(
                            match: re.Match[str], variables: dict[str, Any] = binding.variables
                        ) -> str:
                            value = variables[match.group(1)]
                            return (
                                value
                                if isinstance(value, str)
                                else json.dumps(value, ensure_ascii=False, allow_nan=False)
                            )

                        text = PLACEHOLDER.sub(substitute, text)
                        size = len(text.encode())
                        if path != "SKILL.md" and not entry_loaded:
                            reason = "技能指令未加载"
                        elif size > min(
                            definition.context_budget - skill_used, request.context_budget - used
                        ):
                            reason = "上下文预算不足"
                            issues.append(
                                SkillIssue(
                                    code="SKILL_CONTEXT_LIMIT",
                                    message="技能资料超过上下文预算",
                                    path=path,
                                )
                            )
                        else:
                            used += size
                            skill_used += size
                            entry_loaded = entry_loaded or path == "SKILL.md"
                            loaded.append(
                                SkillLoadedFile(
                                    version_id=skill.version_id,
                                    path=path,
                                    sha256=file.sha256,
                                    text=text,
                                    budget_units=size,
                                    trigger_reason="始终加载"
                                    if (binding.loading_mode or definition.loading_mode)
                                    == "mandatory"
                                    else binding.trigger_reason,
                                )
                            )
                if reason:
                    omitted.append(
                        SkillOmission(version_id=skill.version_id, path=path, reason=reason)
                    )
                    if not file.loadable:
                        issues.append(
                            SkillIssue(
                                code="SKILL_EXECUTION_UNSUPPORTED", message=reason, path=path
                            )
                        )
            omitted.extend(
                SkillOmission(
                    version_id=skill.version_id,
                    path=f.relative_path,
                    reason=f.unavailable_reason or "本次未选择",
                )
                for f in definition.files
                if f.relative_path not in wanted
            )
            if entry_loaded:
                callable_tools.update(
                    set(definition.required_tool_versions) & set(request.authorized_tool_versions)
                )
        # 文件读取后再校验停用、草稿修订和删除状态，避免慢对象请求返回已撤销内容。
        for _, _, skill, _ in resolved:
            await self.port.recheck(context, skill, request.purpose)
        return SkillLoadResult(
            loaded=loaded,
            discoveries=discoveries,
            omitted=omitted,
            issues=issues,
            used_budget=used,
            callable_tool_versions=sorted(callable_tools),
            complete=not issues,
        )
