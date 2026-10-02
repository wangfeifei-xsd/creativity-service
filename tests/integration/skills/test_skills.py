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
    SkillEdit,
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
    assert loaded.omitted[0].reason == "脚本执行未启用"
    test = await env.skills.test(
        env.context,
        frozen.version_id,
        SkillTestInput(revision=frozen.revision, selected_files=("references/a.md",)),
    )
    assert test.result.complete and test.run_id is None
    copied = await env.skills.create_version(
        env.context,
        detail.skill.skill_id,
        SkillVersionCreate(base_version_id=frozen.version_id, version_label="第二版"),
    )
    await env.skills.edit_version(
        env.context,
        copied.version_id,
        SkillVersionEdit(
            revision=copied.revision,
            settings=copied.settings,
            files=(SkillFileInput(relative_path="references/a.md", text="新版资料"),),
        ),
    )
    old = await env.skills.file(env.context, frozen.version_id, "references/a.md")
    assert old.text == "原始资料"
    assert (await env.skills.tests(env.context, frozen.version_id))[0].result.loaded[
        1
    ].text == "原始资料"
    with pytest.raises(ServiceError) as error:
        await env.skills.edit_version(
            env.context,
            frozen.version_id,
            SkillVersionEdit(revision=frozen.revision, settings=frozen.settings),
        )
    assert error.value.code == "VERSION_FROZEN"
    with pytest.raises(ServiceError) as error:
        await env.skills.test(
            env.context,
            frozen.version_id,
            SkillTestInput(revision=frozen.revision, execute_scripts=True),
        )
    assert error.value.code == "SKILL_EXECUTION_UNSUPPORTED"
    disabled = await env.skills.edit(
        env.context,
        detail.skill.skill_id,
        SkillEdit(
            revision=detail.skill.revision,
            name=detail.skill.name,
            description=detail.skill.description,
            owner=detail.skill.owner,
            status="DISABLED",
        ),
    )
    assert disabled.skill.status.label == "已停用"
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
        allowed_data_domains=(env.context.scope.data_scope_id,),
        environments=("test",),
        subject_requirements={"required": False},
    )
    tool_version = await env.tools.management.create_version(
        env.context, tool.tool_id, ToolVersionCreate(version_label="v1", definition=definition)
    )
    frozen_tool = await env.tools.management.freeze(
        env.context, tool_version.version.version_id, tool_version.revision
    )
    created = await env.skills.create(
        env.context,
        body().model_copy(
            update={
                "settings": SkillSettings(
                    tool_requirements=(SkillToolRequirement(tool_code="sum", version_label="v1"),)
                )
            }
        ),
    )
    version = created.versions[0]
    assert (await env.skills.validate(env.context, version.version_id)).valid
    frozen = await env.skills.freeze(env.context, version.version_id, version.revision)
    summary = await env.skills.dependency_summary(env.context, frozen.version_id)
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
