"""MCP 出站复用当前工具权限，不触发独立主体复核。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from creativity_service.core.auth.types import AuthorizationDecision
from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.tools import AdapterRequest
from creativity_service.modules.mcp.contracts import trusted_identity
from tests.test_tools import context, definition


@pytest.mark.parametrize("allowed", [True, False])
async def test_current_tool_access_controls_outbound_identity_without_review(allowed):
    current = context().model_copy(update={"actor_id": None})
    review = AsyncMock(side_effect=AssertionError("渠道调用不得触发主体复核"))
    authorization = SimpleNamespace(
        check=AsyncMock(
            return_value=AuthorizationDecision(
                allowed=allowed, actions=["run:create", "data:read_sensitive"] if allowed else []
            )
        ),
        subjects=SimpleNamespace(read_current=review),
    )
    request = AdapterRequest(
        current,
        {"query": "授权资料"},
        "attempt_a",
        definition(required_scopes=("data:read_sensitive",)),
        "run_a",
    )
    if allowed:
        identity = await trusted_identity(authorization, current, request, "tool_a")
        assert identity["subject_id"] == current.scope.subject_id
        assert identity["resources"] == {"tool": ["tool_a"]}
    else:
        with pytest.raises(ServiceError) as error:
            await trusted_identity(authorization, current, request, "tool_a")
        assert error.value.code == "TOOL_FORBIDDEN"
    review.assert_not_called()
