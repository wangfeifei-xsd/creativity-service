"""由运维显式安装 PostgreSQL 提供的向量扩展，不创建业务函数或修改业务数据。"""

from sqlalchemy import create_engine, text

from creativity_service.core.config import Settings


def main() -> None:
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        print("向量扩展已就绪")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
