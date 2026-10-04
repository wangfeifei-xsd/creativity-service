"""同一次只读操作复用连接，避免逐项借还连接及隐式事务往返。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_current: ContextVar[tuple[AsyncEngine, AsyncConnection, object] | None] = ContextVar(
    "database_read_connection", default=None
)


@asynccontextmanager
async def read_connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    active = _current.get()
    owner = asyncio.current_task()
    if active is not None and active[0] is engine and active[2] is owner:
        yield active[1]
        return
    async with engine.connect() as connection:
        token = _current.set((engine, connection, owner))
        try:
            yield connection
        finally:
            _current.reset(token)
