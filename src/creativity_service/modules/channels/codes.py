"""渠道编码由名称生成，避免把内部标识的编写责任交给用户。"""

from creativity_service.core.name_codes import name_code


def channel_code(name: str) -> str:
    """取名称前四个有效字符的大写首字母，不足四个时循环补齐。"""
    return name_code(name, "渠道")
