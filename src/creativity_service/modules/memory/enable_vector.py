"""验证 Milvus 连接；集合按实际模型维度自动创建。"""

import asyncio

from creativity_service.modules.memory.vector_store import MilvusStore


def main() -> None:
    asyncio.run(MilvusStore().health())
    print("Milvus 已就绪")


if __name__ == "__main__":
    main()
