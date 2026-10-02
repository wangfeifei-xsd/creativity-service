"""首次委托的内容屏障不得绕过历史内容、删除记录或恢复状态。"""

import asyncio

import pytest
from sqlalchemy import delete, insert

from creativity_service.core.database import Repository
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import RecoveryService, barrier_id
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.integrations.subject_content import initialize_subject_content
from creativity_service.storage import metadata as all_metadata

pytestmark = pytest.mark.integration


async def test_initialization_is_serialized_and_cannot_reset_recovery(
    engine, context, authorization
):
    parent = context.model_copy(
        update={"scope": context.scope.model_copy(update={"data_scope_id": "domain_new"})}
    )
    subject = parent.model_copy(
        update={
            "scope": parent.scope.model_copy(
                update={"subject_type": "member", "subject_id": "reader_new"}
            )
        }
    )
    with pytest.raises(ServiceError) as error:
        await initialize_subject_content(engine, subject)
    assert error.value.code == "RECOVERY_BLOCKED"
    recovery = RecoveryService(engine, authorization)
    await recovery.initialize_fresh(parent)
    await recovery.block(parent)
    with pytest.raises(ServiceError) as error:
        await initialize_subject_content(engine, subject)
    assert error.value.code == "RECOVERY_BLOCKED"
    # 删除屏障仅用于模拟不完整恢复；不能依赖数据库默认值补出允许状态。
    table = metadata.tables["recovery_barriers"]
    async with engine.begin() as connection:
        await connection.execute(delete(table).where(table.c.id == barrier_id(parent.scope)))
    await recovery.initialize_fresh(parent)
    await asyncio.gather(*(initialize_subject_content(engine, subject) for _ in range(4)))
    repo = Repository(table, subject.scope)
    async with engine.connect() as connection:
        rows = await repo.find(connection)
    assert len(rows) == 1 and rows[0]["state"] == "READY"
    recovery_id = await recovery.block(subject)
    await initialize_subject_content(engine, subject)
    async with engine.connect() as connection:
        current = await repo.get(connection, barrier_id(subject.scope))
    assert current["state"] == "BLOCKED" and current["recovery_id"] == recovery_id


@pytest.mark.parametrize("history", ["artifacts", "runs", "deletion_markers"])
async def test_missing_subject_barrier_with_history_requires_recovery_proof(
    engine, context, authorization, history
):
    parent = context.model_copy(
        update={"scope": context.scope.model_copy(update={"data_scope_id": "domain_history"})}
    )
    await RecoveryService(engine, authorization).initialize_fresh(parent)
    subject = parent.model_copy(
        update={
            "scope": parent.scope.model_copy(
                update={"subject_type": "member", "subject_id": "reader_history"}
            )
        }
    )
    # 故意模拟只恢复了历史表而丢失屏障的数据库；业务入口不能把它认作新主体。
    async with engine.begin() as connection:
        await connection.execute(
            insert(all_metadata.tables[history]).values(
                id="historical_row", **subject.scope.model_dump()
            )
        )
    with pytest.raises(ServiceError) as error:
        await initialize_subject_content(engine, subject)
    assert error.value.code == "RECOVERY_PROOF_REQUIRED"
    async with engine.connect() as connection:
        assert (
            await Repository(metadata.tables["recovery_barriers"], subject.scope).get(
                connection, barrier_id(subject.scope)
            )
            is None
        )
