"""预授权需同时满足冻结意图与当前授权，调试不得借预授权跳过确认。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.runtime.writes import WriteExecution
from creativity_service.modules.tools.schemas import ToolExecution
from creativity_service.modules.tools.validation import validate_definition
from tests.test_tools import context, definition


def authorized(**changes):
    return definition(
        effect_type="IDEMPOTENT_WRITE",
        idempotency_policy="source_key",
        write_policy={
            "status_tool_version_id": "status",
            "authorization_mode": "preauthorized",
            "allowed_agent_codes": ["case.guide"],
            "allowed_principal_ids": ["user_a"],
            "argument_constraints": {
                "type": "object",
                "properties": {"query": {"const": "允许的内容"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            **changes,
        },
    )


def execution(
    current, *, purpose="production", agent="case.guide", principal="user_a", arguments=None
):
    repo = SimpleNamespace(
        resolve=AsyncMock(
            return_value=(
                {"status": "ACTIVE"},
                {"state": "PUBLISHED", "content": current.model_dump(mode="json")},
            )
        )
    )
    return WriteExecution(
        SimpleNamespace(runs=object()),
        context(principal=principal),
        SimpleNamespace(run_id="run"),
        SimpleNamespace(purpose=purpose, agent_code=agent, content_digest="snapshot"),
        "save",
        ToolExecution(
            run_id="run",
            step_id="save",
            tool_version_id="write",
            arguments=arguments or {"query": "允许的内容"},
        ),
        SimpleNamespace(service=SimpleNamespace(repository=repo)),
        object(),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"allowed_agent_codes": []},
        {"allowed_agent_codes": ["*"]},
        {"allowed_principal_ids": []},
        {"argument_constraints": {}},
        {"argument_constraints": {"type": "object"}},
    ],
)
def test_preauthorization_requires_explicit_closed_boundaries(change):
    with pytest.raises(ServiceError):
        validate_definition(authorized(**change))


@pytest.mark.parametrize(
    "changes",
    [
        {"agent": "other"},
        {"principal": "other"},
        {"arguments": {"query": "越界内容"}},
    ],
)
async def test_execution_rejects_agent_principal_or_arguments_outside_grant(changes):
    value = authorized()
    validate_definition(value)
    with pytest.raises(ServiceError):
        await execution(value, **changes).check_preauthorization(value)


@pytest.mark.parametrize("revoked_side", ["current", "frozen"])
async def test_current_and_frozen_policies_must_both_authorize(revoked_side):
    original, revoked = authorized(), authorized(authorization_mode="per_call")
    with pytest.raises(ServiceError, match="有效的工具预授权范围"):
        await execution(revoked if revoked_side == "current" else original).check_preauthorization(
            revoked if revoked_side == "frozen" else original
        )


async def test_debug_remains_manual_and_evaluation_blocks_real_writes():
    value = authorized()
    debug = execution(value, purpose="debug")
    debug.attempt = AsyncMock()
    await debug.execute(value)
    debug.attempt.assert_awaited_once_with(value, False)
    with pytest.raises(ServiceError, match="不允许真实写入"):
        await execution(value, purpose="evaluation").execute(value)


@pytest.mark.parametrize("state,status", [("DRAFT", "ACTIVE"), ("PUBLISHED", "DISABLED")])
async def test_unpublished_or_disabled_tool_cannot_use_old_frozen_authorization(state, status):
    value = authorized()
    write = execution(value)
    write.executor.service.repository.resolve.return_value = (
        {"status": status},
        {"state": state, "content": value.model_dump(mode="json")},
    )
    with pytest.raises(ServiceError, match="未发布或已停用"):
        await write.check_preauthorization(value)
