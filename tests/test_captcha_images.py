"""验证全部风景素材可生成匹配拼块，且新挑战不会污染缓存底图。"""

import base64
import io
from unittest.mock import Mock

import pytest
from PIL import Image

from creativity_service.modules.iam import captcha_image


def decode_image(value: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(value.split(",", 1)[1])))


@pytest.mark.parametrize("name", captcha_image.LANDSCAPES)
def test_each_landscape_keeps_its_original_and_matches_its_piece(name, monkeypatch):
    monkeypatch.setattr(captcha_image.secrets, "choice", lambda _: name)
    original = captcha_image._landscape(name)
    original_bytes = original.tobytes()
    background, piece = map(decode_image, captcha_image.render_puzzle(150, 50))
    assert background.size == (320, 160) and piece.size == (52, 52)
    assert background.format == piece.format == "PNG"
    assert piece.mode == "RGBA" and piece.getpixel((0, 0))[3] == 0
    assert piece.getpixel((20, 20)) == (*original.getpixel((170, 70)), 255)
    assert background.getpixel((170, 70)) != original.getpixel((170, 70))
    assert background.getpixel((20, 20)) == original.getpixel((20, 20))
    assert original.tobytes() == original_bytes
    # 第二次缺口位于别处，前一次缺口必须恢复为完整原图。
    following, _ = captcha_image.render_puzzle(230, 90)
    assert decode_image(following).getpixel((170, 70)) == original.getpixel((170, 70))


def test_every_challenge_selects_from_the_ten_distinct_bundled_landscapes(monkeypatch):
    names = captcha_image.LANDSCAPES
    assert len(names) == len(set(names)) == 10
    assert {p.stem for p in captcha_image.BACKGROUND_DIRECTORY.glob("*.jpg")} == set(names)
    choose = Mock(side_effect=names)
    monkeypatch.setattr(captcha_image.secrets, "choice", choose)
    backgrounds = {captcha_image.render_puzzle(150, 50)[0] for _ in names}
    assert len(backgrounds) == 10
    assert choose.call_count == 10
    choose.assert_called_with(names)
