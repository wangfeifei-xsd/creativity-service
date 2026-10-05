import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels.codes import channel_code


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("超级赚钱俱乐部", "CJZQ"),
        ("租号", "ZHZH"),
        ("云", "YYYY"),
        ("重庆", "CQCQ"),
        ("AI 平台", "AIPT"),
        ("超级-赚钱", "CJZQ"),
    ],
)
def test_channel_code_uses_four_cyclic_name_initials(name, expected):
    assert channel_code(name) == expected


def test_channel_code_rejects_name_without_supported_characters():
    with pytest.raises(ServiceError, match="渠道名称须包含"):
        channel_code("---")
