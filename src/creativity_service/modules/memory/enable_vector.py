"""验证 Qdrant 连接；集合按实际模型维度自动创建。"""

import asyncio

from creativity_service.modules.memory.vector_store import QdrantStore


def main() -> None:
    asyncio.run(QdrantStore().health())
    print("Qdrant 已就绪")


if __name__ == "__main__":
    main()
