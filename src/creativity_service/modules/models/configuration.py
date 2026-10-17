"""路由保存模型选择和参数覆盖，新任务按当前配置生成独立调用快照。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.models.policy import configuration_digest, validate_parameters
from creativity_service.modules.models.repositories import repository
from creativity_service.modules.models.schemas import FrozenModel


def model_snapshot(scope: Scope, model: dict[str, Any], connection: dict[str, Any]) -> FrozenModel:
    return FrozenModel(
        scope=scope,
        model_id=model["id"],
        model_version_id=model["current_version_id"],
        model_revision=model["revision"],
        model_name=model["name"],
        connection_id=connection["id"],
        connection_version_id=connection["current_version_id"],
        connection_revision=connection["revision"],
        provider_credential_id=connection["credential_ref"],
        protocol=connection["protocol"],
        endpoint=connection["endpoint"],
        provider_model_name=model["provider_model_name"],
        timeout_seconds=connection["timeout_seconds"],
        parameters=model["parameters"],
        parameter_allowlist=model["parameter_allowlist"],
        config_digest=configuration_digest(model, connection),
    )


async def resolve_routes(
    connection: AsyncConnection,
    scope: Scope,
    rows: dict[str, dict[str, Any]],
    *,
    resources: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    """批量解析当前路由，不回写配置，不修改调用方持有的历史内容。"""
    routes = {
        key: row
        for key, row in rows.items()
        if row["resource_type"] == "model_route" and row["id"] == row["resource_id"]
    }
    if not routes:
        return rows
    for row in routes.values():
        if row["content_digest"] != digest(
            {"content": row["content"], "output_schema": row["output_schema"]}
        ):
            raise ServiceError("DEPENDENCY_INVALID", "模型路由配置内容不完整，请重新保存路由", 409)
        for value in row["content"].get("models", []):
            saved = FrozenModel.model_validate(value)
            if (
                saved.scope.channel_id != scope.channel_id
                or saved.scope.environment != scope.environment
            ):
                raise ServiceError("SCOPE_MISMATCH", "模型路由不属于当前渠道或环境", 403)
    resources = resources if resources is not None else {}
    models = resources.setdefault("model", {})
    model_ids = {
        identifier
        for row in routes.values()
        for identifier in [row["content"]["primary_model"], *row["content"]["fallback_models"]]
    }
    models.update(await repository(scope, "models").get_many(connection, model_ids - models.keys()))
    connections = resources.setdefault("model_connection", {})
    connection_ids = {models[i]["connection_id"] for i in model_ids if i in models}
    connections.update(
        await repository(scope, "model_connections").get_many(
            connection, connection_ids - connections.keys()
        )
    )
    resolved = dict(rows)
    for key, row in routes.items():
        content = row["content"]
        snapshots = []
        for identifier in [content["primary_model"], *content["fallback_models"]]:
            model = models.get(identifier)
            conn = connections.get(model["connection_id"]) if model else None
            if model is None or conn is None:
                raise ServiceError(
                    "DEPENDENCY_INVALID", "路由使用的模型或连接已不可用，请检查模型配置", 422
                )
            snapshot = model_snapshot(scope, model, conn)
            parameters = {**snapshot.parameters, **content.get("parameters", {})}
            validate_parameters(snapshot.protocol, snapshot.parameter_allowlist, parameters)
            snapshots.append(snapshot.model_copy(update={"parameters": parameters}))
        current = {**content, "models": [s.model_dump(mode="json") for s in snapshots]}
        dependencies = sorted(
            {value for s in snapshots for value in (s.model_version_id, s.connection_version_id)}
        )
        resolved[key] = {
            **row,
            "content": current,
            "content_digest": digest({"content": current, "output_schema": row["output_schema"]}),
            "dependencies": dependencies,
            "dependencies_digest": digest(dependencies),
        }
    return resolved
