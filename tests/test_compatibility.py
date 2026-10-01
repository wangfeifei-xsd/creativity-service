"""验证关键 SDK 在同一锁定环境中可组合导入和运行。"""

from typing import TypedDict


def test_runtime_sdk_compatibility(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from alembic.config import Config
    from celery import Celery
    from langgraph.graph import END, START, StateGraph
    from litellm import completion
    from mcp import ClientSession
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import dialect

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
