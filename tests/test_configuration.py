"""可导入制品与安全配置的静态边界，禁止任意代码或外部契约解析。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.schemas import AgentCreate
from creativity_service.modules.agents.validation import static_issues
from creativity_service.modules.skills.packages import unpack, validate_files
from creativity_service.modules.skills.schemas import SkillSettings, SkillToolRequirement
from examples.agents.prepare import prepare


@pytest.mark.parametrize("name", ["text-brief", "archive-answer"])
def test_portable_packages_match_source_and_agent_manifests(name):
    root = Path("examples/skills")
    files = {
        str(path.relative_to(root / name)): path.read_bytes()
        for path in (root / name).rglob("*")
        if path.is_file()
    }
    package = validate_files(files)
    stored = (root / "packages" / f"{name}.zip").read_bytes()
    assert stored == package.archive(portable=True)
    assert not unpack(stored).settings.tool_bindings
    manifest = json.loads(Path(f"examples/agents/manifests/{name}.json").read_text())
    assert manifest["tool_requirements"] == [
        r.model_dump(mode="json") for r in package.settings.tool_requirements
    ]
    template = json.loads(Path(f"examples/agents/{name}.json").read_text())
    assert not static_issues(
        prepare(template, {key: "target_" + key for key in manifest["resources"]}).definition
    )
    with pytest.raises(ValueError, match="缺少显式资源绑定"):
        prepare(template, {})


def test_new_configuration_rejects_arbitrary_operators_unbound_files_and_bad_schema():
    body = json.loads(Path("examples/agents/archive-answer.json").read_text())
    body["definition"]["steps"][-1]["operator"] = "eval"
    with pytest.raises(ValidationError):
        AgentCreate.model_validate(body)
    body["definition"]["steps"][-1]["operator"] = "object"
    body["definition"]["bindings"]["skill_loading"][0]["version_id"] = "unbound_skill"
    assert any(
        i.path == "bindings.skill_loading"
        for i in static_issues(AgentCreate.model_validate(body).definition)
    )
    package = unpack(Path("examples/skills/packages/text-brief.zip").read_bytes())
    settings = SkillSettings(
        tool_requirements=(
            SkillToolRequirement(
                tool_code="remote",
                version_label="v1",
                input_schema={"type": "object", "$ref": "https://invalid.example/schema"},
            ),
        )
    )
    with pytest.raises(ServiceError, match="契约不合法"):
        validate_files(package.files, settings)
    with pytest.raises(ServiceError, match="渠道身份"):
        validate_files(
            {
                "SKILL.md": b"---\nname: invalid\ndescription: invalid\n"
                b"tool_bindings: {tool: source_version}\n---\ninvalid"
            }
        )
