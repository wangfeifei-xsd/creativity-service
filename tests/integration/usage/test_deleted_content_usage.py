"""删除后的迟报用量仍可幂等结算，账本不保留回调中的内容。"""

import pytest

from creativity_service.core.deletion.ledger import DeletionLedger
from tests.integration.usage.test_ledger import event, prepare, price, stored

pytestmark = pytest.mark.integration


async def test_late_usage_keeps_numbers_and_discards_content(usage_env):
    env = usage_env
    await price(env)
    plan = await prepare(env)
    await DeletionLedger().record(env.scope, "run", plan.run_id, "CONTENT_REQUESTED", "test")
    report = event(env, plan).model_copy(
        update={
            "raw_usage": {
                "input_tokens": 50,
                "output_tokens": 20,
                "response": "private deleted text",
            }
        }
    )
    first = await env.usage.ledger.settle(report)
    repeated = await env.usage.ledger.settle(report)
    assert first["amount"] == repeated["amount"] and first["input_tokens"] == 50
    records = await stored(env, "usage_events", attempt_id=plan.attempt_id)
    assert len(records) == 1 and "private deleted text" not in str(records)
    assert records[0]["raw_usage"]["input_tokens"] == 50
