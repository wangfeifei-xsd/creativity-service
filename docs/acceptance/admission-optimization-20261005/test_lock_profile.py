"""沿用原负载准备数据，单独分解二十并发受理的锁等待。"""

import contextvars
import json
import os
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

import pytest
from sqlalchemy import event

import creativity_service.core.database as database
from creativity_service.core.primitives import RunInput
from creativity_service.modules.agents.runtime import PreparedAgentResolver
from tests.integration.acceptance import test_performance as original
from tests.integration.agents.conftest import agent_env
from tests.integration.channels.conftest import channel_env
from tests.integration.runtime.conftest import runtime_env
from tests.support.independence_evidence import ROOT, tree

__all__ = ["agent_env", "channel_env", "runtime_env"]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "test",
                "independent_actions": ["release:publish", "data:read_sensitive"],
            }
        ],
        indirect=True,
    ),
]


class Complete(Exception):
    pass


async def test_profile(runtime_env, monkeypatch):
    env = runtime_env
    source_before = tree(ROOT, ["src", "pyproject.toml", "uv.lock", "deploy"])["sha256"]
    initial_load = os.getloadavg()
    active = contextvars.ContextVar("profile", default=None)
    records = []
    measuring = False

    def traced(label, function, request=False):
        async def call(*args, **kwargs):
            value = active.get()
            token = None
            if request and measuring:
                value = {"counts": Counter(), "ms": Counter(), "locks": []}
                token = active.set(value)
            if value is None:
                return await function(*args, **kwargs)
            started = perf_counter()
            value["counts"][label] += 1
            try:
                return await function(*args, **kwargs)
            finally:
                value["ms"][label] += (perf_counter() - started) * 1000
                if token is not None:
                    records.append(value)
                    active.reset(token)

        return call

    for owner, method, label, request in (
        (type(env.runs), "admit_run", "admit", True),
        (env.runs.resolver, "prepare", "prepare", False),
        (PreparedAgentResolver, "validate_in", "prepare_validate", False),
        (env.runs.resolver, "validate_in", "validate_in", False),
        (env.runs.versions, "snapshot_in", "snapshot", False),
        (env.runs.budgets, "admit", "budget", False),
    ):
        monkeypatch.setattr(owner, method, traced(label, getattr(owner, method), request))

    acquire = database.acquire_locks

    async def acquire_locks(connection, keys):
        started = perf_counter()
        try:
            await acquire(connection, keys)
        finally:
            if value := active.get():
                value["locks"].append(
                    {
                        "ms": (perf_counter() - started) * 1000,
                        "resources": sorted(
                            {f"{k.channel_id}:{k.resource_type}:{k.shared}" for k in keys}
                        ),
                    }
                )

    monkeypatch.setattr(database, "acquire_locks", acquire_locks)

    def before(connection, cursor, statement, parameters, context, many):
        context.profile = (active.get(), perf_counter())

    def after(connection, cursor, statement, parameters, context, many):
        value, started = context.profile
        if value is None:
            return
        category = "lock_sql" if "pg_advisory_xact_lock" in statement else "other_sql"
        value["counts"][category] += 1
        value["ms"][category] += (perf_counter() - started) * 1000
        value["counts"]["sql:" + statement.split("\n")[0][:140]] += 1

    event.listen(env.engine.sync_engine, "before_cursor_execute", before)
    event.listen(env.engine.sync_engine, "after_cursor_execute", after)

    @asynccontextmanager
    async def clients(environment, tenants):
        nonlocal measuring
        measuring = True

        async def submit(index):
            context = tenants[index % 4].manager.context
            receipt = await env.runs.admit_run(
                context,
                RunInput(agent_code="perf_0", input={"request": "请处理"}),
                f"profile-{index}",
            )
            return context, receipt.run_id

        accepted = await original.parallel(*(submit(index) for index in range(20)))
        measuring = False
        await original.parallel(
            *(env.runs.cancel(context, identifier) for context, identifier in accepted)
        )
        raise Complete()
        yield

    monkeypatch.setattr(original, "http_clients", clients)
    try:
        with pytest.raises(Complete):
            await original.test_twenty_clients_management_and_admission(env)
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", before)
        event.remove(env.engine.sync_engine, "after_cursor_execute", after)
        Path(__file__).with_name("lock-profile.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2)
        )
        source_after = tree(ROOT, ["src", "pyproject.toml", "uv.lock", "deploy"])["sha256"]
        Path(__file__).with_name("profile-environment.json").write_text(
            json.dumps(
                {
                    "service_sha256": source_before,
                    "unchanged_during_measurement": source_before == source_after,
                    "initial_load_average": initial_load,
                    "final_load_average": os.getloadavg(),
                    "samples": len(records),
                    "boundary": "探针数据只用于定位，不能替代无探针性能验收",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        assert source_before == source_after
