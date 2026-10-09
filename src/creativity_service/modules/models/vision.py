"""生成视觉验证题；图片与答案随调试描述冻结，答案只用于本地校验。"""

import base64
import secrets
from io import BytesIO

from PIL import Image, ImageDraw

from creativity_service.modules.models.schemas import CaseDefinition


def vision_case(definition: CaseDefinition) -> CaseDefinition:
    colors = {"red": "#e02020", "green": "#169b36", "blue": "#2058e0", "yellow": "#f5d020"}
    shapes = ("circle", "square", "triangle", "diamond")
    picture = Image.new("RGB", (480, 144), "white")
    draw = ImageDraw.Draw(picture)
    answer = []
    for index in range(4):
        color, shape = secrets.choice(tuple(colors)), secrets.choice(shapes)
        answer.append({"color": color, "shape": shape})
        left, right = index * 120 + 24, index * 120 + 96
        center = index * 120 + 60
        fill = colors[color]
        if shape == "circle":
            draw.ellipse((left, 36, right, 108), fill=fill)
        elif shape == "square":
            draw.rectangle((left, 36, right, 108), fill=fill)
        elif shape == "triangle":
            draw.polygon(((center, 30), (left, 108), (right, 108)), fill=fill)
        else:
            draw.polygon(((center, 24), (right, 72), (center, 120), (left, 72)), fill=fill)
    buffer = BytesIO()
    picture.save(buffer, format="PNG")
    return definition.model_copy(
        update={
            "images": ["data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()],
            "output_mode": "prompt",
            "output_schema": {"type": "object", "const": {"shapes": answer}},
        }
    )
