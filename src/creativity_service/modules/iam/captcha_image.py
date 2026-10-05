"""从系统渠道的风景图库随机生成拼图，不暴露原图路径与缺口坐标。"""

import base64
import io
import secrets
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from creativity_service.core.primitives import unavailable

WIDTH, HEIGHT, PIECE_SIZE = 320, 160, 52
BACKGROUND_DIRECTORY = Path(__file__).with_name("assets") / "captcha" / "system"
LANDSCAPES = (
    "green-hills",
    "alpine-lake",
    "coast",
    "sand-dunes",
    "waterfall",
    "autumn",
    "flower-field",
    "snow-mountains",
    "aurora",
    "forest",
)


@lru_cache(maxsize=len(LANDSCAPES))
def _landscape(name: str) -> Image.Image:
    # 平台公共登录素材归系统渠道；有限缓存不保存用户或业务渠道数据。
    try:
        with Image.open(BACKGROUND_DIRECTORY / f"{name}.jpg") as source:
            return ImageOps.fit(
                source.convert("RGB"), (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS
            )
    except OSError as exc:
        raise unavailable("验证码图片") from exc


def _data_url(image: Image.Image) -> str:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def render_puzzle(x: int, y: int) -> tuple[str, str]:
    # 每次新挑战独立选图；只修改副本，避免缓存原图积累旧缺口。
    image = _landscape(secrets.choice(LANDSCAPES)).copy()
    mask = Image.new("L", (PIECE_SIZE, PIECE_SIZE), 0)
    shape = ImageDraw.Draw(mask)
    shape.rounded_rectangle((1, 10, 41, 50), radius=4, fill=255)
    shape.ellipse((14, 0, 29, 18), fill=255)
    shape.ellipse((33, 23, 51, 38), fill=255)
    shape.ellipse((-8, 23, 10, 38), fill=0)
    piece = image.crop((x, y, x + PIECE_SIZE, y + PIECE_SIZE)).convert("RGBA")
    piece.putalpha(mask)
    outline = mask.filter(ImageFilter.MaxFilter(3))
    image.paste("#f2f5f6", (x, y), outline)
    image.paste("#465664", (x, y), mask.point(lambda value: int(value * 0.78)))
    return _data_url(image), _data_url(piece)
