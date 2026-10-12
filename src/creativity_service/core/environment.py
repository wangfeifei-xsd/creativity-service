"""统一选择 dev、test、prod 各自独立的配置文件。"""

import os
from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import ENV_FILE_SENTINEL, DotenvType

Environment = Literal["dev", "test", "prod"]


def normalize_environment(value: str) -> Environment:
    if value in {"dev", "development"}:
        return "dev"
    if value == "test":
        return "test"
    if value in {"prod", "production"}:
        return "prod"
    raise ValueError("CREATIVITY_ENVIRONMENT 必须为 dev、test 或 prod")


def environment_files(environment: str | None = None) -> tuple[Path, ...]:
    """每个环境只读取对应文件，进程环境变量仍优先。"""
    if environment is None:
        variables = {key.lower(): value for key, value in os.environ.items()}
        environment = variables.get("creativity_environment", "dev")
    selected = normalize_environment(environment)
    return (Path(f".env.{selected}"),)


class EnvironmentSettings(BaseSettings):
    """各模块共享环境选择，保留显式文件及禁用文件加载的测试入口。"""

    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    def __init__(self, _env_file: DotenvType | None = ENV_FILE_SENTINEL, **values: Any) -> None:
        if _env_file == ENV_FILE_SENTINEL:
            _env_file = environment_files(values.get("environment"))
        super().__init__(_env_file=_env_file, **values)
