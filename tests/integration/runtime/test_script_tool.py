"""真实技能包经隔离工具与统一运行执行，输出保留缺失值和确定性计算结果。"""

import os

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import digest
from creativity_service.integrations.sandbox import (
    ContainerSandbox,
    SandboxProfile,
    SandboxSettings,
)
from creativity_service.integrations.tools.script import resolve_script
from creativity_service.modules.skills.schemas import SkillCreate, SkillFileInput, SkillVersionEdit
from creativity_service.modules.tools.schemas import (
    ScriptBinding,
    ToolCreate,
    ToolDefinition,
    ToolTestInput,
    ToolVersionCreate,
)
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_execution import pytestmark

__all__ = ["pytestmark"]


async def test_frozen_skill_script_in_real_container_uses_unified_run(runtime_env):
    image = os.getenv("CREATIVITY_SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("需要固定摘要隔离镜像")
    env = runtime_env
    skill = await env.skills.create(
        env.context,
        SkillCreate(
            skill_code="calculator",
            name="通用计算",
            description="处理已授权输入",
            owner="验收",
            instructions="缺失值保持为空。",
        ),
    )
    version = skill.versions[0]
    version = await env.skills.edit_version(
        env.context,
        version.version_id,
        SkillVersionEdit(
            revision=version.revision,
            settings=version.settings,
            files=(
                SkillFileInput(
                    relative_path="scripts/calc.py",
                    text=(
                        "import sys,json\nfrom decimal import Decimal\n"
                        "v=json.load(sys.stdin)\n"
                        'total=sum(Decimal(x) for x in v["values"] if x is not None)\n'
                        'missing=sum(x is None for x in v["values"])\n'
                        'print(json.dumps({"sum":str(total),"missing":missing}))'
                    ),
                ),
            ),
        ),
    )
    frozen = await env.skills.freeze(env.context, version.version_id, version.revision)
    profile = SandboxProfile(
        profile_id="test",
        name="隔离验收",
        image=image,
        mode="python",
        channels={env.context.scope.channel_id},
        command=("python3", "-I", "-B"),
        seconds=10,
    )
    sandbox = ContainerSandbox(SandboxSettings(_env_file=None, profiles=[profile]))
    env.tools.registry._resolvers.insert(
        0, lambda scope, binding: resolve_script(env.skills, sandbox, scope, binding)
    )
    tool = await env.tools.management.create(
        env.context,
        ToolCreate(
            tool_code="calculate",
            name="确定性计算",
            description="固定技能版本隔离运行",
            owner="验收",
            source_type="sandbox",
        ),
    )
    definition = ToolDefinition(
        input_schema={
            "type": "object",
            "properties": {"values": {"type": "array", "items": {"type": ["string", "null"]}}},
            "required": ["values"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"sum": {"type": "string"}, "missing": {"type": "integer"}},
            "required": ["sum", "missing"],
        },
        model_fields_allowed=("values",),
        binding={
            "adapter_key": "sandbox_python",
            "implementation_version": "1",
            "script": ScriptBinding(
                profile_id="test",
                profile_digest=digest(profile.model_dump(mode="json")),
                skill_version_id=frozen.version_id,
                path="scripts/calc.py",
            ),
        },
        effect_type="READ_ONLY",
        environments=("test",),
        subject_requirements={"required": False},
    )
    version = await env.tools.management.create_version(
        env.context, tool.tool_id, ToolVersionCreate(version_label="验收版", definition=definition)
    )
    receipt = await env.tools.management.test(
        env.context,
        version.version.version_id,
        ToolTestInput(revision=version.revision, arguments={"values": ["0.1", "0.2", None]}),
    )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id),
        "script-worker",
        env.runtime,
    )
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.model_dump()
    assert result.result.data["data"]["sum"] == "0.3"
    assert result.result.data["data"]["missing"] == 1
