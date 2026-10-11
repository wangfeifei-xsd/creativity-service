"""验证关键 SDK 在同一锁定环境中可组合导入和运行。"""

import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict


def test_runtime_sdk_compatibility(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("LITELLM_MODE", "PRODUCTION")
    from alembic.config import Config
    from celery import Celery
    from langgraph.graph import END, START, StateGraph
    from litellm import completion
    from mcp import ClientSession
    from sqlalchemy import text
    from sqlalchemy.dialects.mysql import dialect

    class State(TypedDict):
        value: int

    graph = StateGraph(State)
    graph.add_node("increment", lambda state: {"value": state["value"] + 1})
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    assert graph.compile().invoke({"value": 1}) == {"value": 2}
    assert callable(completion)
    assert ClientSession
    assert Celery
    assert Config
    assert str(text("SELECT 1").compile(dialect=dialect())) == "SELECT 1"


def test_model_call_does_not_load_dotenv_into_process(tmp_path):
    # 使用新进程覆盖首次导入；测试仅走本地协议夹具，不连接真实供应商。
    sentinel = "CREATIVITY_SDK_IMPORT_SENTINEL"
    (tmp_path / ".env").write_text(f"{sentinel}=unexpected\n", encoding="utf-8")
    environment = {k: v for k, v in os.environ.items() if k not in {sentinel, "LITELLM_MODE"}}
    root = Path(__file__).resolve().parents[1]
    environment["PYTHONPATH"] = os.pathsep.join([str(root), str(root / "src")])
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio, os\n"
            "from tests.models.test_protocols import test_protocol_text_and_raw_usage\n"
            "asyncio.run(test_protocol_text_and_raw_usage('chat_completions'))\n"
            f"assert {sentinel!r} not in os.environ, '模型库隐式加载了项目环境文件'\n",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
