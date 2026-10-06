"""SKL-A01—A06：真实持久化、对象存储、并发发布与身份隔离。"""

import asyncio
import base64

import pytest
from sqlalchemy import create_engine

from creativity_service.core.config import Settings
from creativity_service.core.database.audit import audit_database
from creativity_service.core.deletion import ContentRef
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.skills.packages import entry, unpack, validate_files
from creativity_service.modules.skills.repositories import repository
from creativity_service.modules.skills.schemas import (
    SkillBinding,
    SkillCreate,
    SkillFileInput,
    SkillImport,
    SkillLoadRequest,
    SkillRelease,
    SkillSettings,
    SkillTestInput,
    SkillToolRequirement,
    SkillVersionCreate,
    SkillVersionEdit,
)
from creativity_service.modules.tools.schemas import ToolCreate, ToolDefinition, ToolVersionCreate
from tests.integration.channels.conftest import provision

pytestmark = pytest.mark.integration


def body(code="rental_intent"):
    return SkillCreate(
        skill_code=code,
        name="租赁诉求解析",
        description="从输入提取已知条件",
        owner="业务负责人",
        instructions="只提取用户已经确认的事实。",
    )


async def test_version_summaries_do_not_download_historical_packages(skills_env):
    env = skills_env
    detail = await env.skills.create(env.context, body())
    original = detail.versions[0]
    with pytest.raises(ServiceError, match="直接修改"):
        await env.skills.create_version(
            env.context,
            detail.skill.skill_id,
            SkillVersionCreate(base_version_id=original.version_id, version_label="不再创建版本"),
        )
    env.store.reads.clear()
    summaries = await env.skills.detail(env.context, detail.skill.skill_id)
    assert len(summaries.versions) == 1
    assert not env.store.reads
    selected = await env.skills.version(env.context, original.version_id)
    assert selected.instruction_preview == "只提取用户已经确认的事实。"
    assert len(env.store.reads) == 1


async def test_runtime_skill_validation_reads_one_batch_for_multiple_skills(skills_env):
    from creativity_service.core.database import transaction
    from creativity_service.core.locking import read_key
    from creativity_service.core.versioning import version_view
    from creativity_service.modules.skills.services import SkillVersionValidator
    from tests.integration.agents.test_read_queries import statements

    env = skills_env
    versions = []
    for index in range(5):
        detail = await env.skills.create(env.context, body(f"batch_skill_{index}"))
        async with env.engine.connect() as connection:
            stored = await repository("resource_versions", env.context.scope).get(
                connection, detail.versions[0].version_id
            )
        versions.append(version_view(stored))
    counts = []
    for size in (1, 5):
        keys = [
            read_key(key)
            for version in versions[:size]
            for key in env.skills.versions.keys(env.context.scope, version.version_id, "read")
        ]
        with statements(env.engine) as queries:
            async with transaction(env.engine, env.context.scope, keys) as uow:
                await SkillVersionValidator(env.skills).validate_many(
                    uow, env.context, versions[:size]
                )
        counts.append(len(queries))
    assert counts[0] == counts[1]


async def test_create_freeze_fork_load_and_release_preserve_history(skills_env):
    env = skills_env
    detail = await env.skills.create(env.context, body())
    version = detail.versions[0]
    edited = await env.skills.edit_version(
        env.context,
        version.version_id,
        SkillVersionEdit(
            revision=version.revision,
            settings=version.settings,
            files=(
                SkillFileInput(relative_path="references/a.md", text="原始资料"),
                SkillFileInput(
                    relative_path="scripts/a.py", text="raise RuntimeError('must not run')"
                ),
            ),
        ),
    )
    frozen = await env.skills.freeze(env.context, edited.version_id, edited.revision)
    released = await env.skills.release(
        env.context, detail.skill.skill_id, SkillRelease(version_id=frozen.version_id)
    )
    assert released.release_version_id == frozen.version_id
    env.store.reads.clear()
    discovery = await env.skills.loader.load(
        env.context, SkillLoadRequest(bindings=(SkillBinding(version_id=frozen.version_id),))
    )
    assert discovery.complete and not discovery.loaded and not env.store.reads
    loaded = await env.skills.loader.load(
        env.context,
        SkillLoadRequest(
            bindings=(
                SkillBinding(
                    version_id=frozen.version_id, selected=True, selected_files=("references/a.md",)
                ),
            )
        ),
    )
    assert [f.path for f in loaded.loaded] == ["SKILL.md", "references/a.md"]
    assert loaded.omitted[0].reason == "通过隔离工具执行"
    test = await env.skills.test(
        env.context,
        frozen.version_id,
        SkillTestInput(revision=frozen.revision, selected_files=("references/a.md",)),
    )
    assert test.result.complete and test.run_id is None
    from creativity_service.core.versioning import version_view
    from creativity_service.modules.skills.frozen import FrozenSkillPort

    _, old_row = await env.skills.raw(env.context, frozen.version_id)
    fixed = FrozenSkillPort(env.skills, (version_view(old_row),))
    admitted = await fixed.resolve(env.context, frozen.version_id, "runtime")
    edited = await env.skills.edit_version(
        env.context,
        frozen.version_id,
        SkillVersionEdit(
            revision=frozen.revision,
            settings=frozen.settings,
            files=(SkillFileInput(relative_path="references/a.md", text="新版资料"),),
        ),
    )
    assert edited.status.value == "PUBLISHED"
    current = await env.skills.file(env.context, frozen.version_id, "references/a.md")
    assert current.text == "新版资料"
    await fixed.recheck(env.context, admitted, "runtime")
    assert (await fixed.read_files(env.context, admitted, ("references/a.md",), "runtime"))[
        "references/a.md"
    ].decode() == "原始资料"
    assert (await env.skills.tests(env.context, frozen.version_id))[0].result.loaded[
        1
    ].text == "原始资料"
    with pytest.raises(ServiceError) as error:
        await env.skills.test(
            env.context,
            frozen.version_id,
            SkillTestInput(revision=frozen.revision, execute_scripts=True),
        )
    assert error.value.code == "SKILL_EXECUTION_UNSUPPORTED"
    from creativity_service.modules.resources.schemas import ResourceMutation
    from creativity_service.modules.resources.services import ResourceManagement

    resources = ResourceManagement(
        env.engine, env.iam.authorization, {"skill": env.skills.versions.validator}
    )
    item = (await resources.summaries(env.context, "skill", [detail.skill.skill_id]))[0]
    await resources.mutate(
        env.context,
        "skill",
        detail.skill.skill_id,
        "unpublish",
        ResourceMutation(
            revision=item.revision, configuration_revision=item.configuration_revision
        ),
    )
    item = (await resources.summaries(env.context, "skill", [detail.skill.skill_id]))[0]
    assert item.status.label == "未发布"
    result = await env.skills.loader.load(
        env.context,
        SkillLoadRequest(bindings=(SkillBinding(version_id=frozen.version_id, selected=True),)),
    )
    assert not result.complete and not result.loaded


async def test_import_validation_export_scope_and_cross_channel(skills_env):
    env = skills_env
    package = validate_files(
        {
            "SKILL.md": entry("业务技能", "只读事实", "只使用明确提供的数据。"),
            "references/sample.json": b'{"example": true}',
        }
    )
    imported = await env.skills.import_package(
        env.context,
        SkillImport(
            skill_code="portable",
            owner="负责人",
            archive_base64=base64.b64encode(package.archive(portable=True)).decode(),
        ),
    )
    version = imported.versions[0]
    artifact = await env.skills.export(env.context, version.version_id)
    response = await env.client.get(artifact.download_path)
    assert response.status_code == 200
    exported = unpack(response.content)
    assert exported.package_hash == package.package_hash
    other = await provision(env, "other")
    context = await env.iam.authentication.authenticate(other.token.access_token, "management")
    with pytest.raises(ServiceError) as error:
        await env.skills.version(context, version.version_id)
    assert error.value.code == "NOT_FOUND"
    copied = await env.skills.import_package(
        context,
        SkillImport(
            skill_code="portable",
            owner="负责人",
            archive_base64=base64.b64encode(response.content).decode(),
        ),
    )
    assert (
        copied.skill.skill_id != imported.skill.skill_id
        and copied.versions[0].version_id != version.version_id
    )
    assert copied.versions[0].package_hash == version.package_hash


async def test_concurrent_codes_edits_and_invalid_import_leave_no_manifest(skills_env):
    env = skills_env
    results = await asyncio.gather(
        *(env.skills.create(env.context, body()) for _ in range(2)), return_exceptions=True
    )
    assert (
        sum(isinstance(result, ServiceError) and result.code == "CODE_EXISTS" for result in results)
        == 1
    )
    detail = next(result for result in results if not isinstance(result, BaseException))
    version = detail.versions[0]
    results = await asyncio.gather(
        *(
            env.skills.edit_version(
                env.context,
                version.version_id,
                SkillVersionEdit(
                    revision=1,
                    settings=version.settings,
                    files=(SkillFileInput(relative_path="ref.txt", text=value),),
                ),
            )
            for value in ("甲", "乙")
        ),
        return_exceptions=True,
    )
    assert (
        sum(
            isinstance(result, ServiceError) and result.code == "REVISION_CONFLICT"
            for result in results
        )
        == 1
    )
    response = await env.client.post(
        "/admin/v1/skills/imports",
        json={"skill_code": "invalid", "owner": "负责人", "archive_base64": "abcd"},
    )
    assert response.status_code == 422
    async with env.engine.connect() as connection:
        assert not await repository("skills", env.context.scope).find(
            connection, skill_code="invalid"
        )
        artifacts = await repository("artifacts", env.context.scope).find(
            connection, state="STAGED"
        )
    assert len(artifacts) >= 2
    assert await env.client._transport.app.state.core.artifacts.cleanup_orphans(env.context) >= 2


async def test_dependency_rebinding_unauthorized_tools_and_missing_capabilities(skills_env):
    env = skills_env
    tool = await env.tools.management.create(
        env.context,
        ToolCreate(
            tool_code="sum",
            name="精确合计",
            description="合计",
            owner="负责人",
            source_type="builtin",
        ),
    )
    definition = ToolDefinition(
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "object"},
        model_fields_allowed=(),
        binding={"adapter_key": "decimal_sum", "implementation_version": "1"},
        effect_type="READ_ONLY",
        environments=("test",),
        subject_requirements={"required": False},
    )
    tool_version = await env.tools.management.create_version(
        env.context, tool.tool_id, ToolVersionCreate(version_label="v1", definition=definition)
    )
    frozen_tool = await env.tools.management.freeze(
        env.context, tool_version.version.version_id, tool_version.revision
    )
    from creativity_service.modules.tools.schemas import ToolRelease

    await env.tools.management.release(
        env.context, tool.tool_id, ToolRelease(version_id=frozen_tool.version.version_id)
    )
    created = await env.skills.create(
        env.context,
        body().model_copy(
            update={
                "settings": SkillSettings(
                    tool_requirements=(SkillToolRequirement(tool_code="sum", version_label="v1"),),
                    tool_bindings={"sum": frozen_tool.version.version_id},
                )
            }
        ),
    )
    version = created.versions[0]
    assert (await env.skills.validate(env.context, version.version_id)).valid
    frozen = await env.skills.freeze(env.context, version.version_id, version.revision)
    summary = await env.skills.dependency_summary(env.context, frozen.version_id)
    from creativity_service.modules.skills.schemas import SkillDefinition

    _, stored = await env.skills.raw(env.context, frozen.version_id)
    legacy = dict(stored["content"])
    legacy.pop("tool_bindings")
    restored = await env.skills.package(
        env.context, created.skill.skill_id, SkillDefinition.model_validate(legacy)
    )
    assert restored.settings.tool_bindings == {"sum": frozen_tool.version.version_id}
    assert restored.package_hash == frozen.package_hash
    assert summary.required_tool_versions == (frozen_tool.version.version_id,)
    denied = await env.skills.loader.load(
        env.context,
        SkillLoadRequest(bindings=(SkillBinding(version_id=frozen.version_id, selected=True),)),
    )
    assert denied.issues[0].code == "SKILL_DEPENDENCY_MISSING"
    result = await env.skills.loader.load(
        env.context,
        SkillLoadRequest(
            bindings=(SkillBinding(version_id=frozen.version_id, selected=True),),
            authorized_tool_versions=(frozen_tool.version.version_id,),
        ),
    )
    assert result.complete and result.callable_tool_versions == [frozen_tool.version.version_id]
    other = await provision(env, "other")
    other_context = await env.iam.authentication.authenticate(
        other.token.access_token, "management"
    )
    portable = await env.skills.export(env.context, frozen.version_id)
    data = (await env.client.get(portable.download_path)).content
    imported = await env.skills.import_package(
        other_context,
        SkillImport(
            skill_code="sum_skill", owner="负责人", archive_base64=base64.b64encode(data).decode()
        ),
    )
    check = await env.skills.validate(other_context, imported.versions[0].version_id)
    assert not check.valid and check.dependencies[0].version_id is None
    invalid = await env.skills.create(
        env.context,
        body("capabilities").model_copy(
            update={"settings": SkillSettings(required_model_capabilities=("vision",))}
        ),
    )
    assert not (await env.skills.validate(env.context, invalid.versions[0].version_id)).valid
    await env.tools.management.disable(env.context, tool.tool_id, tool.revision)
    with pytest.raises(ServiceError) as error:
        await env.skills.dependency_summary(env.context, frozen.version_id)
    assert error.value.code == "SKILL_DEPENDENCY_MISSING"


async def test_deletion_blocks_packages_and_schema_is_compliant(skills_env):
    env = skills_env
    detail = await env.skills.create(env.context, body())
    version = detail.versions[0]
    await env.skills.deletion.mark(
        env.context, ContentRef("skill", detail.skill.skill_id), "manual"
    )
    with pytest.raises(ServiceError) as error:
        await env.skills.version(env.context, version.version_id)
    assert error.value.code == "CONTENT_DELETED"
    sync_engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with sync_engine.connect() as connection:
            assert audit_database(connection, env.schema) == []
    finally:
        sync_engine.dispose()


async def test_api_rejects_scope_override_and_corrupt_objects(skills_env):
    env = skills_env
    response = await env.client.post(
        "/admin/v1/skills", json={**body().model_dump(mode="json"), "channel_id": "foreign"}
    )
    assert response.status_code == 422
    response = await env.client.post("/admin/v1/skills", json=body().model_dump(mode="json"))
    assert response.status_code == 201
    version_id = response.json()["versions"][0]["version_id"]
    _, version = await env.skills.raw(env.context, version_id)
    async with env.engine.connect() as connection:
        artifact = await repository("artifacts", env.context.scope).get(
            connection, version["content"]["artifact_id"]
        )
    await env.store.put(artifact["object_key"], b"corrupted", "application/zip")
    response = await env.client.post(
        f"/admin/v1/skill-versions/{version_id}/tests", json={"revision": 1}
    )
    assert response.status_code == 503 and response.json()["error"]["code"] == "ARTIFACT_CORRUPT"


async def test_file_deleted_after_read_is_not_returned_to_runtime(skills_env):
    from creativity_service.core.deletion import DeletionService
    from creativity_service.modules.skills.authorization import PackageAuthorization

    env = skills_env
    detail = await env.skills.create(env.context, body())
    version = detail.versions[0]
    original = env.skills.read_files
    deletion = DeletionService(
        env.engine,
        PackageAuthorization(
            env.iam.authorization, env.context, detail.skill.skill_id, "content:delete"
        ),
    )

    async def read_then_delete(context, skill, paths, purpose):
        files = await original(context, skill, paths, purpose)
        await deletion.mark(context, ContentRef("artifact", skill.definition.artifact_id), "manual")
        return files

    env.skills.read_files = read_then_delete
    with pytest.raises(ServiceError) as error:
        await env.skills.test(env.context, version.version_id, SkillTestInput(revision=1))
    assert error.value.code == "CONTENT_DELETED"
