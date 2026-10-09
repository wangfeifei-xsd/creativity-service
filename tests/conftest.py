"""单元测试使用显式配置，避免依赖开发者环境。"""

import os

import pytest

from creativity_service.core.config import Settings


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in os.environ:
        if key.startswith("CREATIVITY_"):
            monkeypatch.delenv(key)
    return Settings(
        _env_file=None,
        environment="test",
        database_url="mysql+pymysql://test:test@127.0.0.1:1/test",
        redis_cache_url="redis://127.0.0.1:1/0",
        redis_auth_url="redis://127.0.0.1:1/1",
        celery_broker_url="redis://127.0.0.1:1/2",
        celery_result_url="redis://127.0.0.1:1/3",
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_access_key_id="test",
        s3_secret_access_key="test-secret",
        s3_bucket="test-bucket",
        cors_origins=["http://localhost:5173"],
    )
