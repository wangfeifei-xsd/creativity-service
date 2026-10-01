"""数据库先记录撤销事实，Redis 清理失败可由命令幂等补偿。"""

from creativity_service.core.auth.tokens import TokenStore
from creativity_service.core.auth.types import Revocation
from creativity_service.core.context import ControlScope, Scope
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.iam.repositories import (
    IdentityRepository,
    one,
    policy_key,
    save,
    to_state,
)


async def enqueue(uow: UnitOfWork, record_id: str, kind: str, target_id: str) -> Revocation:
    return to_state(
        Revocation,
        await save(
            uow,
            "iam_revocations",
            record_id,
            {
                "kind": kind,
                "target_id": target_id,
                "cutoff_at": utcnow(),
                "completed_at": None,
            },
        ),
    )


class RevocationService:
    def __init__(self, repository: IdentityRepository, tokens: TokenStore) -> None:
        self.repository, self.tokens = repository, tokens

    async def complete(self, item: Revocation) -> bool:
        try:
            await self.tokens.revoke(item)
        except ServiceError as exc:
            if exc.status == 503:
                return False
            raise
        scope: Scope | ControlScope = (
            ControlScope(purpose="identity_lookup", actor_id="revocation_service")
            if item.channel_id == "system"
            else Scope(channel_id=item.channel_id, environment="dev")
        )
        # 补偿记录只有渠道归属；此内部范围不会进入任何业务或内容仓储。
        async with transaction(
            self.repository.engine,
            scope,
            [policy_key(item.channel_id), record_key(item.channel_id, "iam_revocations", item.id)],
        ) as uow:
            row = await one(uow.connection, "iam_revocations", item.channel_id, id=item.id)
            if row and row["completed_at"] is None:
                await save(
                    uow, "iam_revocations", item.id, {"completed_at": utcnow()}, row["revision"]
                )
        return True

    async def reconcile(self, limit: int = 100) -> tuple[int, int]:
        completed = pending = 0
        for item in await self.repository.pending_revocations(limit):
            if await self.complete(item):
                completed += 1
            else:
                pending += 1
        return completed, pending
