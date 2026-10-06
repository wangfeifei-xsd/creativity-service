"""供应商与渠道共用四位名称首字母规则。"""

import pytest

from creativity_service.core.name_codes import name_code
from creativity_service.core.primitives import ServiceError


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("供应商", "GYSG"),
        ("云", "YYYY"),
        ("重庆", "CQCQ"),
        ("OpenAI", "OPEN"),
        ("AI", "AIAI"),
        (" A-I 平台 ", "AIPT"),
        ("１２云", "12Y1"),
    ],
)
def test_provider_name_initials_are_four_characters(name, code):
    assert name_code(name, "供应商") == code


def test_provider_name_rejects_unsupported_characters():
    with pytest.raises(ServiceError, match="供应商名称须包含"):
        name_code("---", "供应商")
