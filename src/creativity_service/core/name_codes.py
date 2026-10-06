"""从可读名称生成固定四位编码，供各资源复用。"""

import re
import unicodedata

from pypinyin import Style, lazy_pinyin

from creativity_service.core.primitives import ServiceError


def name_code(name: str, label: str) -> str:
    """取前四个有效字符的首字母；不足四位按原顺序循环补齐。"""
    characters = [
        character
        for character in unicodedata.normalize("NFKC", name).strip()
        if character.isalnum()
    ]
    if not characters:
        raise ServiceError("VALIDATION_ERROR", f"{label}名称须包含中文、英文字母或数字", 422)
    source = "".join(characters[index % len(characters)] for index in range(4))
    initials = lazy_pinyin(source, style=Style.FIRST_LETTER)
    code = "".join(
        match.group(0).upper()
        for value in initials
        for character in value
        if (match := re.search(r"[A-Za-z0-9]", unicodedata.normalize("NFKD", character)))
    )
    if len(code) != 4:
        raise ServiceError("VALIDATION_ERROR", f"{label}名称前四个有效字符无法生成编码", 422)
    return code
