"""渠道编码由名称生成，避免把内部标识的编写责任交给用户。"""

import re
import unicodedata

from pypinyin import Style, lazy_pinyin

from creativity_service.core.primitives import ServiceError


def channel_code(name: str) -> str:
    """取名称前四个有效字符的大写首字母，不足四个时循环补齐。"""
    characters = [
        character
        for character in unicodedata.normalize("NFKC", name).strip()
        if character.isalnum()
    ]
    if not characters:
        raise ServiceError("VALIDATION_ERROR", "渠道名称须包含中文、英文字母或数字", 422)
    source = "".join(characters[index % len(characters)] for index in range(4))
    initials = lazy_pinyin(source, style=Style.FIRST_LETTER)
    code = "".join(
        match.group(0).upper()
        for value in initials
        for character in value
        if (match := re.search(r"[A-Za-z0-9]", unicodedata.normalize("NFKD", character)))
    )
    if len(code) != 4:
        raise ServiceError("VALIDATION_ERROR", "渠道名称前四个有效字符无法生成编码", 422)
    return code
