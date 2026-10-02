"""真实 Redis/Celery 传递渠道消息，以及远端返回后进程硬退出验证。"""

import asyncio
import os
import sys
from queue import Queue
from uuid import uuid4

import pytest
from celery import Celery, Task, current_app
from celery.contrib.testing.worker import start_worker

from creativity_service.core.config import Settings
from creativity_service.modules.usage.schemas import AttemptPlan
from creativity_service.workers.runs import CeleryPublisher
from tests.integration.runs.test_runs import envelope, expire_lease, values

pytestmark = pytest.mark.integration


async def test_real_celery_duplicate_messages_are_only_wakeups(env):
    receipt = await env.runs.admit_run(env.context, env.request, "real_queue")
    name = f"test_runs_{uuid4().hex}"
    old_app = current_app._get_current_object()
    app = Celery(name, broker=Settings().celery_broker_url.get_secret_value())
    app.conf.update(
        task_default_queue=name,
        task_serializer="json",
        accept_content=["json"],
        broker_transport_options={"global_keyprefix": name + ":"},
        task_ignore_result=True,
    )
    received = Queue()

    class Receiver(Task):
        name = "runs.execute"
        ignore_result = True

        def run(self, message):
            received.put(message)

    app.finalize()
    app.register_task(Receiver())
    try:
        app.set_current()
        with start_worker(app, pool="solo", perform_ping_check=False, queues=[name]):
            publisher = CeleryPublisher()
            message = envelope(env, receipt.run_id)
            await publisher.publish(message)
            await publisher.publish(message)
            first = await asyncio.to_thread(received.get, timeout=10)
            second = await asyncio.to_thread(received.get, timeout=10)
            assert first == second == message.model_dump()
            first_lease, second_lease = await asyncio.gather(
                env.runs.claim_lease(message, "worker_a"), env.runs.claim_lease(message, "worker_b")
            )
            assert sum(lease is not None for lease in (first_lease, second_lease)) == 1
    finally:
        app.close()
        old_app.set_current()


async def test_process_exit_after_remote_return_keeps_attempt_and_pending_cost(
    env, database_schema
):
    receipt = await env.runs.admit_run(env.context, env.request, "process_exit")
    lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "exiting_worker")
    await env.runs.start_step(lease, "model")
    attempt = await env.runs.start_attempt(
        lease,
        "model",
        AttemptPlan(
            run_id=receipt.run_id,
            attempt_id="candidate",
            agent_id="agent_one",
            model_id="model_one",
            connection_id="model_connection",
            purpose="production",
            input_tokens=20,
            max_output_tokens=50,
        ),
    )
    program = """
import asyncio, json, os
from sqlalchemy.ext.asyncio import create_async_engine
from creativity_service.core.versioning import VersionService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.usage.services import UsageService
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runs.schemas import Lease
class InternalAuthorization:
    async def require(self, *args):
        pass
async def run():
    engine = create_async_engine(os.environ['RUN_TEST_DSN'],
        connect_args={'options': '-csearch_path=' + os.environ['RUN_TEST_SCHEMA']})
    authorization = InternalAuthorization()
    budgets = BudgetService(engine)
    runs = RunService(engine, authorization, VersionService(engine, authorization),
        budgets, UsageService(engine, budgets))
    lease = Lease.model_validate(json.loads(os.environ['RUN_TEST_LEASE']))
    assert await runs.mark_sent(lease, os.environ['RUN_TEST_ATTEMPT'])
    returned = {'value': 1}
    assert returned
    os._exit(9)
asyncio.run(run())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        program,
        env={
            **os.environ,
            "RUN_TEST_DSN": env.engine.url.render_as_string(hide_password=False),
            "RUN_TEST_SCHEMA": database_schema,
            "RUN_TEST_LEASE": lease.model_dump_json(),
            "RUN_TEST_ATTEMPT": attempt["id"],
        },
    )
    assert await asyncio.wait_for(process.wait(), timeout=20) == 9
    await expire_lease(env, receipt.run_id)
    assert await env.runs.claim_lease(envelope(env, receipt.run_id), "recovery_worker") is None
    old = (await values(env, "attempts"))[0]
    assert old["state"] == "UNKNOWN" and old["usage_id"] is not None
    assert (await env.runs.get_run(env.context, receipt.run_id)).usage_summary["pending_count"] == 1
