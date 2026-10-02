"""出站身份只包含最终一次源端复核仍然允许的执行权限。"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from creativity_service.core.auth.types import AuthorizationDecision, SubjectAuthority
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.tools import AdapterRequest
from creativity_service.modules.mcp.contracts import trusted_identity
from tests.test_tools import context, definition


async def test_current_review_revoking_execution_cannot_be_reexpanded_by_identity():
    current = context().model_copy(update={"actor_id": None})
    authority = SubjectAuthority(
        scope=current.scope,
        expires_at=utcnow() + timedelta(seconds=30),
        actions=frozenset({"data:read_sensitive"}),
        agent_actions=frozenset({"data:read_sensitive"}),
        resources={"tool": frozenset({"tool_a"})},
    )
    authorization = SimpleNamespace(
        check=AsyncMock(
            return_value=AuthorizationDecision(
                allowed=True, actions=["run:create", "data:read_sensitive"]
            )
        ),
        subjects=SimpleNamespace(read_current=AsyncMock(return_value=authority)),
    )
    request = AdapterRequest(
        current,
        {"query": "授权资料"},
        "attempt_a",
        definition(required_scopes=("data:read_sensitive",)),
        "run_a",
    )
    with pytest.raises(ServiceError) as error:
        await trusted_identity(authorization, current, request, "tool_a")
    assert error.value.code == "TOOL_FORBIDDEN"
