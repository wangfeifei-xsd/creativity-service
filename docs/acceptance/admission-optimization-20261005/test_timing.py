"""沿用原验收负载，另存本次结果，避免覆盖历史基线。"""

import json
from pathlib import Path

import pytest

from tests.integration.acceptance import test_performance as original
from tests.integration.agents.conftest import agent_env
from tests.integration.channels.conftest import channel_env
from tests.integration.runtime.conftest import runtime_env

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


async def test_timing(runtime_env, monkeypatch):
    def capture(name, payload):
        target = Path(__file__).with_name(name)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print("复测结果：", target, flush=True)

    monkeypatch.setattr(original, "record", capture)
    await original.test_twenty_clients_management_and_admission(runtime_env)
