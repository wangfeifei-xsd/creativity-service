"""路由配置与业务调用分离，正式发布和执行仍保留权限及能力门禁。"""

from collections import Counter

import pytest
from sqlalchemy import event, func, select, update

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.models.repositories import repository
from creativity_service.modules.models.schemas import ReleaseInput, RouteInput, RouteVersionInput
from creativity_service.storage import metadata
from tests.integration.channels.conftest import login, provision
from tests.integration.models.test_models import complete, setup

pytestmark = pytest.mark.integration


async def test_route_configuration_saves_without_execution_or_capability_evidence(
    channel_env,
):
    env = channel_env
    tenant, services, _, _, _, model = await setup(env)
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=tenant.channel.channel_id,
            environment="test",
        ),
    )
    manager = await env.iam.authentication.admin_session(token.access_token, "route-config")
    route = await services.routing.create(manager, RouteInput(code="config", name="待验证路由"))
    response = await env.client.post(
        f"/admin/v1/model-routes/{route.id}/versions",
        headers={"Authorization": "Bearer " + token.access_token},
        json={"label": "v1", "primary_model": model.id},
    )
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["content"]["primary_model"] == model.id
    assert saved["content"]["fallback_models"] == []
    assert (await services.routing.list_items(manager)).items[0].released_version_id is None
    listed = await env.client.get(
        f"/admin/v1/model-routes/{route.id}/versions",
        headers={"Authorization": "Bearer " + token.access_token},
    )
    assert listed.status_code == 200, listed.text
    publish = listed.json()[0]["actions"][0]
    assert publish["label"] == "发布" and not publish["enabled"]
    assert "尚未通过" in publish["disabled_reason"]
    with pytest.raises(ServiceError) as execution:
        await services.routing.resolve_route(manager.context, saved["version_id"])
    assert execution.value.status == 422
    assert execution.value.code == "MODEL_CAPABILITY_UNSUPPORTED"
    with pytest.raises(ServiceError) as release:
        await services.routing.release(
            manager, route.id, ReleaseInput(version_id=saved["version_id"])
        )
    assert release.value.code == "MODEL_CAPABILITY_UNSUPPORTED"
    async with env.engine.connect() as db:
        mapped = await repository(manager.context.scope, "models").get(db, model.id)
        assert mapped["capabilities"] == {} and mapped["verified_at"] is None
        assert await db.scalar(select(func.count()).select_from(metadata.tables["runs"])) == 0


async def test_unverified_route_requires_capability_evidence_before_release_and_execution(
    channel_env,
):
    tenant, services, _, _, _, model = await setup(channel_env, publish=True)
    route = await services.routing.create(
        tenant.manager, RouteInput(code="unverified", name="待验证路由")
    )
    version = await services.routing.create_version(
        tenant.manager, route.id, RouteVersionInput(label="v1", primary_model=model.id)
    )
    unavailable = (await services.routing.versions(tenant.manager, route.id))[0].actions[0]
    assert not unavailable.enabled and "尚未通过" in unavailable.disabled_reason
    with pytest.raises(ServiceError) as release:
        await services.routing.release(
            tenant.manager, route.id, ReleaseInput(version_id=version.version_id)
        )
    assert release.value.code == "MODEL_CAPABILITY_UNSUPPORTED"
    with pytest.raises(ServiceError) as execution:
        await services.routing.resolve_route(tenant.manager.context, version.version_id)
    assert execution.value.code == "MODEL_CAPABILITY_UNSUPPORTED"
    await complete(services, tenant, model)
    assert (await services.routing.versions(tenant.manager, route.id))[0].actions[0].enabled
    await services.routing.release(
        tenant.manager, route.id, ReleaseInput(version_id=version.version_id)
    )
    assert (await services.routing.list_items(tenant.manager)).items[
        0
    ].released_version_id == version.version_id
    current = (await services.routing.versions(tenant.manager, route.id))[0].actions[0]
    assert not current.enabled and "当前发布版本" in current.disabled_reason


async def test_route_publish_action_explains_missing_environment_permission(channel_env):
    tenant, services, _, _, _, model = await setup(channel_env)
    grants = metadata.tables["resource_grants"]
    async with channel_env.engine.begin() as db:
        rows = (
            (
                await db.execute(
                    select(grants).where(grants.c.channel_id == tenant.channel.channel_id)
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            await db.execute(
                update(grants)
                .where(grants.c.channel_id == tenant.channel.channel_id, grants.c.id == row["id"])
                .values(
                    allowed_actions=[a for a in row["allowed_actions"] if a != "release:publish"]
                )
            )
    route = await services.routing.create(
        tenant.manager, RouteInput(code="no-release", name="未获发布授权的路由")
    )
    await services.routing.create_version(
        tenant.manager, route.id, RouteVersionInput(label="v1", primary_model=model.id)
    )
    action = (await services.routing.versions(tenant.manager, route.id))[0].actions[0]
    assert (
        not action.enabled
        and action.disabled_reason == "当前角色未获此环境的发布权限，请联系有授权权限的管理员"
    )


async def test_route_candidate_reads_are_batched_and_cross_channel_references_rejected(channel_env):
    env = channel_env
    tenant, services, _, _, model_body, model = await setup(env, publish=True)
    models = [model]
    for index in range(3):
        models.append(
            await services.configuration.save_model(
                tenant.manager,
                model_body.model_copy(
                    update={"model_code": f"fallback-{index}", "name": f"回退模型{index}"}
                ),
            )
        )
    route = await services.routing.create(tenant.manager, RouteInput(code="batch", name="批量路由"))
    statements = []

    def record(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT") and "FROM " in statement:
            statements.append(statement)

    counts, read_counts = [], []
    event.listen(env.engine.sync_engine, "before_cursor_execute", record)
    try:
        for size in (1, 4):
            statements.clear()
            version = await services.routing.create_version(
                tenant.manager,
                route.id,
                RouteVersionInput(
                    label=f"v{size}",
                    primary_model=model.id,
                    fallback_models=[item.id for item in models[1:size]],
                ),
            )
            assert len(version.content["models"]) == size
            counts.append(len(statements))
            tables = Counter(
                table
                for query in statements
                for table in ("models", "model_connections")
                if f"FROM {table} " in query
            )
            # 事务前冻结与写入事务内复核各批量读取一次，候选数量不增加查询。
            assert tables == {"models": 2, "model_connections": 2}
            statements.clear()
            await services.routing.versions(tenant.manager, route.id)
            read_counts.append(len(statements))
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", record)
    assert counts[0] == counts[1], counts
    assert read_counts[0] == read_counts[1], read_counts
    tables = Counter(
        table
        for query in statements
        for table in ("models", "model_connections")
        if f"FROM {table} " in query
    )
    assert tables == {"models": 1, "model_connections": 1}
    other = await provision(env, "other", "club")
    other_route = await services.routing.create(
        other.manager, RouteInput(code="foreign", name="隔离路由")
    )
    with pytest.raises(ServiceError) as denied:
        await services.routing.create_version(
            other.manager, other_route.id, RouteVersionInput(label="v1", primary_model=model.id)
        )
    assert denied.value.status == 404


async def test_configuration_scope_publishes_without_business_mapping_or_model_execution(
    channel_env,
):
    env = channel_env
    tenant, services, _, _, _, model = await setup(env, configuration_only=True)
    await complete(services, tenant, model)
    route = await services.routing.create(
        tenant.manager, RouteInput(code="config-release", name="渠道路由")
    )
    version = await services.routing.create_version(
        tenant.manager, route.id, RouteVersionInput(label="v1", primary_model=model.id)
    )
    # 发布只需要配置与发布授权；移除执行授权后仍能发布。
    grants = metadata.tables["resource_grants"]
    async with env.engine.begin() as db:
        for row in (
            await db.execute(select(grants).where(grants.c.channel_id == tenant.channel.channel_id))
        ).mappings():
            await db.execute(
                update(grants)
                .where(grants.c.id == row["id"], grants.c.channel_id == tenant.channel.channel_id)
                .values(allowed_actions=[a for a in row["allowed_actions"] if a != "run:create"])
            )
    assert not (
        await env.iam.authorization.check(tenant.manager.context, "run:create", "model", model.id)
    ).allowed
    assert (await services.routing.versions(tenant.manager, route.id))[0].actions[0].enabled
    await services.routing.release(
        tenant.manager, route.id, ReleaseInput(version_id=version.version_id)
    )
    assert (await services.routing.list_items(tenant.manager)).items[
        0
    ].released_version_id == version.version_id
    with pytest.raises(ServiceError) as execution:
        await services.routing.resolve_route(tenant.manager.context, version.version_id)
    assert execution.value.status == 403
