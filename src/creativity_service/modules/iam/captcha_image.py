"""生成随机几何拼图；只向浏览器发送位图，不暴露缺口的结构化坐标。"""

import base64
import io
import secrets

from PIL import Image, ImageDraw, ImageFilter

WIDTH, HEIGHT, PIECE_SIZE = 320, 160, 52


def _data_url(image: Image.Image) -> str:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def render_puzzle(x: int, y: int) -> tuple[str, str]:
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for row in range(HEIGHT):
        shade = int(235 - row * 0.2)
        draw.line((0, row, WIDTH, row), fill=(shade - 9, shade - 3, shade))
    sun_x, sun_y = 40 + secrets.randbelow(240), 15 + secrets.randbelow(40)
    draw.ellipse((sun_x - 18, sun_y - 18, sun_x + 18, sun_y + 18), fill="#f7f3e9")
    for layer in range(3):
        floor = 100 + layer * 30
        for start in range(-40, WIDTH, 60):
            peak_x = start + 30 + secrets.randbelow(40)
            peak_y = floor - 40 - secrets.randbelow(45)
            draw.polygon(
                [(start - 35, HEIGHT), (peak_x, peak_y), (start + 130, HEIGHT)],
                fill=(135 - layer * 22, 155 - layer * 20, 169 - layer * 18),
            )
            draw.polygon(
                [(peak_x, peak_y), (peak_x + 12, HEIGHT), (start + 130, HEIGHT)],
                fill=(164 - layer * 22, 181 - layer * 20, 192 - layer * 18),
            )
    # 随机细线增加纹理，拼块与原图使用同一底图裁切。
    for _ in range(12):
        start = secrets.randbelow(WIDTH)
        draw.line((start, 0, start - 100, HEIGHT), fill="#b1bdc8", width=1)
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
