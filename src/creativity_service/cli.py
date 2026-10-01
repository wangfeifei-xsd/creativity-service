"""API 独立启动命令。"""

import uvicorn


def main() -> None:
    uvicorn.run(
        "creativity_service.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        access_log=False,
    )
