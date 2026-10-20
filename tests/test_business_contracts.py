"""公共契约只要求渠道凭据；业务用户标识可选。"""

import json
from pathlib import Path

from creativity_service.modules.integrations.export import artifacts


def test_channel_contract_artifacts_match_current_api():
    for name, value in artifacts().items():
        assert json.loads((Path("contracts/integrations") / name).read_text()) == value
    schema = artifacts()["openapi-v3.json"]
    assert "/api/v1/auth/token" in schema["paths"]
    assert not any("delegation" in path or "subject-review" in path for path in schema["paths"])
    headers = schema["paths"]["/api/v1/runs"]["post"]["parameters"]
    subject = {item["name"]: item for item in headers if item["in"] == "header"}
    assert "X-Business-Delegation" not in subject
    assert not subject["X-Subject-Type"]["required"]
    assert not subject["X-Subject-ID"]["required"]
