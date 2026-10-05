"""SKL-A01/A02/A03/A06：归档边界、确定性加载与脚本禁止执行。"""

import io
import json
import stat
import tarfile
import zipfile
from dataclasses import replace

import pytest

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.skills.loader import ResolvedSkill, SkillLoader
from creativity_service.modules.skills.packages import (
    MAX_FILE,
    PORTABLE,
    check_export,
    entry,
    unpack,
    validate_files,
)
from creativity_service.modules.skills.schemas import (
    SkillBinding,
    SkillDefinition,
    SkillLoadRequest,
    SkillSettings,
    SkillVariable,
)


def archive(items):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path, data in items:
            bundle.writestr(path, data)
    return buffer.getvalue()


def content():
    return entry("租赁诉求解析", "提取用户确认的条件", "先核对用户提供的事实。")


@pytest.mark.parametrize(
    "files",
    [
        [("readme.md", b"missing")],
        [("../SKILL.md", content())],
        [("/SKILL.md", content())],
        [("C:\\SKILL.md", content())],
        [("SKILL.md", content()), ("a/../b.txt", b"x")],
        [("SKILL.md", content()), ("Skill.md", b"x")],
        [("SKILL.md", content()), ("a", b"x"), ("a/b.txt", b"x")],
        [("SKILL.md", content()), ("big.txt", b"x" * (MAX_FILE + 1))],
        [("SKILL.md", content()), *[(f"file{i}.txt", b"x") for i in range(128)]],
        [("SKILL.md", entry("名称", "描述", "[外部](../secret.txt)"))],
        [("SKILL.md", entry("名称", "描述", "[外部](https://example.com/policy.md)"))],
        [("SKILL.md", entry("名称", "描述", "[外部](%2e%2e/private.md)"))],
        [("SKILL.md", b"---\nname: a\nname: b\ndescription: x\n---\nx")],
        [("SKILL.md", b"---\nname: &a [*a]\ndescription: x\n---\nx")],
    ],
)
def test_invalid_packages_are_rejected(files):
    with pytest.raises(ServiceError) as error:
        unpack(archive(files))
    assert error.value.code == "SKILL_FORMAT_INVALID"


def test_links_and_duplicate_entries_rejected():
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for files in (
        [("SKILL.md", content()), (link, b"/tmp")],
        [("SKILL.md", content()), ("x", b"a"), ("x", b"b")],
    ):
        with pytest.raises(ServiceError):
            unpack(archive(files))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo("SKILL.md")
        info.size = len(content())
        bundle.addfile(info, io.BytesIO(content()))
        link = tarfile.TarInfo("link")
        link.type = tarfile.LNKTYPE
        link.linkname = "/tmp/outside"
        bundle.addfile(link)
    with pytest.raises(ServiceError):
        unpack(buffer.getvalue())


def test_metadata_portability_and_actual_hash():
    original = (
        b"---\nname: test\ndescription: test skill\nlicense: MIT\n"
        b"metadata:\n  category: rental\n---\nUse facts."
    )
    package = unpack(archive([("SKILL.md", original), ("references/a.json", b'{"x":1}')]))
    assert package.metadata["metadata"]["category"] == "rental"
    assert unpack(package.archive(portable=True)).package_hash == package.package_hash
    changed = validate_files({**package.files, "references/a.json": b'{"x":2}'})
    assert changed.package_hash != package.package_hash
    with pytest.raises(ServiceError):
        unpack(
            archive(
                [
                    ("SKILL.md", content()),
                    (
                        PORTABLE,
                        json.dumps(
                            {
                                "format": "skill-package-v1",
                                "settings": {"required_tool_versions": ["source_id"]},
                            }
                        ).encode(),
                    ),
                ]
            )
        )


def test_sensitive_export_is_refused_and_scripts_are_inert(tmp_path):
    marker = tmp_path / "executed"
    package = validate_files(
        {
            "SKILL.md": content(),
            "scripts/run.py": f"open({str(marker)!r}, 'w').write('bad')".encode(),
            "key.txt": b"api_key: private-value",
        }
    )
    assert (
        next(f for f in package.manifest if f.relative_path.endswith(".py")).unavailable_reason
        == "通过隔离工具执行"
    )
    with pytest.raises(ServiceError) as error:
        check_export(package)
    assert error.value.code == "SKILL_EXPORT_SENSITIVE"
    assert not marker.exists()


class MemoryPort:
    def __init__(self, settings=None, variables=False):
        self.package = validate_files(
            {
                "SKILL.md": entry("技能", "说明", "指令 {{ query }}" if variables else "指令"),
                "refs/a.md": "资料甲".encode(),
                "refs/b.txt": "资料乙".encode(),
                "scripts/a.py": b"raise RuntimeError()",
            },
            settings,
        )
        definition = SkillDefinition(
            **self.package.settings.model_dump(),
            package_hash=self.package.package_hash,
            metadata=self.package.metadata,
            files=self.package.manifest,
            artifact_id="artifact_a",
            required_tool_versions=(),
        )
        self.skills = {
            "version_a": ResolvedSkill("skill_a", "version_a", 1, "PUBLISHED", True, definition)
        }
        self.reads = []
        self.rechecks = []

    async def resolve(self, context, version_id, purpose):
        return self.skills[version_id]

    async def read_files(self, context, skill, paths, purpose):
        self.reads.append(paths)
        return {p: self.package.files[p] for p in paths}

    async def recheck(self, context, skill, purpose):
        self.rechecks.append(skill.version_id)


def context():
    return AuthContext(
        scope=Scope(channel_id="channel_a", environment="test"),
        principal_type="management",
        principal_id="admin",
        actor_id="admin",
        request_id="request_a",
    )


async def test_discovery_does_not_read_any_files_and_selected_only_loads_requested():
    port = MemoryPort()
    loader = SkillLoader(port)
    result = await loader.load(
        context(), SkillLoadRequest(bindings=(SkillBinding(version_id="version_a"),))
    )
    assert result.complete and not result.loaded and len(result.discoveries) == 1
    assert not port.reads
    result = await loader.load(
        context(),
        SkillLoadRequest(
            bindings=(
                SkillBinding(version_id="version_a", selected=True, selected_files=("refs/a.md",)),
            )
        ),
    )
    assert [f.path for f in result.loaded] == ["SKILL.md", "refs/a.md"]
    assert port.reads == [("SKILL.md", "refs/a.md")]
    assert {f.path for f in result.omitted} == {"refs/b.txt", "scripts/a.py"}
    assert port.rechecks == ["version_a", "version_a"]


async def test_budget_omission_and_unsupported_scripts_are_explicit():
    port = MemoryPort(SkillSettings(loading_mode="mandatory", context_budget=8))
    result = await SkillLoader(port).load(
        context(),
        SkillLoadRequest(
            bindings=(
                SkillBinding(version_id="version_a", selected_files=("refs/a.md", "scripts/a.py")),
            )
        ),
    )
    assert [f.path for f in result.loaded] == ["SKILL.md"]
    assert {i.code for i in result.issues} == {"SKILL_CONTEXT_LIMIT", "SKILL_EXECUTION_UNSUPPORTED"}
    assert result.used_budget == 6 and not result.complete


async def test_unauthorized_tools_and_conflicting_skills_never_produce_context():
    port = MemoryPort()
    skill = port.skills["version_a"]
    port.skills["version_a"] = replace(
        skill,
        definition=skill.definition.model_copy(update={"required_tool_versions": ("tool_a",)}),
    )
    result = await SkillLoader(port).load(
        context(), SkillLoadRequest(bindings=(SkillBinding(version_id="version_a", selected=True),))
    )
    assert (
        not result.loaded and result.issues[0].code == "SKILL_DEPENDENCY_MISSING" and not port.reads
    )
    definition = skill.definition.model_copy(
        update={"loading_mode": "mandatory", "conflict_groups": ("risk",)}
    )
    port.skills["version_a"] = replace(skill, definition=definition)
    port.skills["version_b"] = replace(
        skill, version_id="version_b", skill_id="skill_b", definition=definition
    )
    result = await SkillLoader(port).load(
        context(),
        SkillLoadRequest(
            bindings=(SkillBinding(version_id="version_a"), SkillBinding(version_id="version_b"))
        ),
    )
    assert not result.loaded and result.issues[0].code == "SKILL_CONFLICT"


async def test_priority_variables_and_current_state():
    port = MemoryPort(
        SkillSettings(
            loading_mode="mandatory",
            input_variables=(SkillVariable(name="query", label="用户诉求"),),
        ),
        variables=True,
    )
    skill = port.skills["version_a"]
    port.skills["version_b"] = replace(skill, version_id="version_b", skill_id="skill_b")
    bindings = (
        SkillBinding(version_id="version_a", variables={"query": "甲"}),
        SkillBinding(version_id="version_b", variables={"query": "乙"}, priority=-1),
    )
    result = await SkillLoader(port).load(context(), SkillLoadRequest(bindings=bindings))
    assert [f.text for f in result.loaded] == ["指令 乙", "指令 甲"]
    port.skills["version_a"] = replace(skill, active=False)
    result = await SkillLoader(port).load(context(), SkillLoadRequest(bindings=bindings))
    assert not result.loaded and not result.complete
