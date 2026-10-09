"""视觉证据沿用配置失效与渠道边界，不覆盖其他能力验证结果。"""

import pytest

from creativity_service.modules.models.schemas import CaseResult
from creativity_service.modules.models.schemas import TestCompletion as Completion
from creativity_service.modules.models.schemas import TestInput as CasesInput
from creativity_service.modules.models.testing import CASES
from tests.integration.models.test_models import Executor, complete, setup

pytestmark = pytest.mark.integration


async def test_vision_evidence_preserves_other_capabilities_and_invalidates_with_config(
    channel_env,
):
    tenant, services, _, _, model_input, model = await setup(channel_env)
    await complete(services, tenant, model)
    before = await services.configuration.detail(tenant.manager, model.id)
    existing = [c for c in before.capabilities if c.capability != "vision"]
    assert next(c for c in before.capabilities if c.capability == "vision").reason == (
        "当前配置尚未通过真实验证"
    )
    executor = Executor()
    services.configuration.executor = executor
    for evidence, passed, state in [
        ("fixture", True, "UNVERIFIED"),
        ("live", True, "SUPPORTED"),
        ("live", False, "UNSUPPORTED"),
    ]:
        test = await services.testing.create(tenant.manager, model.id, CasesInput(cases=["vision"]))
        case = executor.submissions[-1].cases[0]
        assert case.images and case.output_schema and case.output_mode == "prompt"
        await services.testing.complete(
            tenant.manager.context,
            test.id,
            Completion(
                run_id=test.run_id,
                config_digest=test.config_digest,
                results=[CaseResult(case="vision", passed=passed, attempt_ids=["attempt_vision"])],
                latency_ms=10,
                evidence=evidence,
            ),
        )
        current = await services.configuration.detail(tenant.manager, model.id)
        assert [c for c in current.capabilities if c.capability != "vision"] == existing
        vision = next(c for c in current.capabilities if c.capability == "vision")
        assert vision.state == state
        assert bool(vision.verified_at) == (evidence == "live")
    changed = await services.configuration.save_model(
        tenant.manager,
        model_input.model_copy(
            update={"provider_model_name": "changed", "revision": current.revision}
        ),
        model.id,
    )
    assert all(c.state == "UNVERIFIED" for c in changed.capabilities)


async def test_all_seven_cases_available_through_management_api(channel_env):
    tenant, services, *_, model = await setup(channel_env)
    channel_env.client.headers["Authorization"] = f"Bearer {tenant.token.access_token}"
    response = await channel_env.client.get("/admin/v1/model-test-cases")
    assert response.status_code == 200
    assert {case["case"] for case in response.json()} == set(CASES)
    services.configuration.executor = Executor()
    response = await channel_env.client.post(
        f"/admin/v1/models/{model.id}/tests", json={"cases": list(CASES)}
    )
    assert response.status_code == 201, response.text
    assert len(response.json()["cases"]) == 7
