"""配置检查不执行源码；分页检查不依赖模型推断。"""

import pytest

from creativity_service.core.primitives import ServiceError, digest
from creativity_service.integrations.sandbox import (
    ContainerSandbox,
    SandboxProfile,
    SandboxSettings,
)
from creativity_service.modules.agents.registry import templates
from creativity_service.modules.agents.schemas import NodePython, StepPagination
from creativity_service.modules.agents.scripts import (
    check_source,
    environment_options,
    resolve_profile,
)
from creativity_service.modules.agents.validation import static_issues
from creativity_service.modules.runtime.pagination import check_page, page_arguments


@pytest.mark.parametrize(
    "source",
    [
        "def transform(:",
        "return {}",
        "async def transform(value, context): pass",
        "def transform(value): pass",
        "@print\ndef transform(value, context): return value",
    ],
)
def test_invalid_source_is_rejected(source):
    with pytest.raises(ServiceError, match="Python|transform"):
        check_source(source)


def test_syntax_check_never_executes_top_level_code():
    check_source(
        'raise RuntimeError("不能在宿主运行")\ndef transform(value, context): return value'
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"dependency": "model"},
        {"prompt_id": "prompt"},
        {"operator": "object"},
        {"failure_handler": "tool"},
        {"script": None},
    ],
)
def test_script_node_rejects_external_capabilities(changes):
    definition = templates()[1].definition
    script = NodePython(
        source="def transform(value, context): return value",
        profile_id="local",
        profile_digest="0" * 64,
    )
    step = definition.steps[0].model_copy(
        update={"kind": "script", "script": script, "failure_policy": "fail", "max_retries": 0}
    )
    assert not static_issues(definition.model_copy(update={"steps": (step,)}))
    invalid = step.model_copy(update=changes)
    assert static_issues(definition.model_copy(update={"steps": (invalid,)}))


def test_profile_is_channel_scoped_and_digest_pinned():
    profile = SandboxProfile(
        profile_id="python",
        name="测试",
        image="sha256:" + "a" * 64,
        command=("python3", "-I", "-B"),
        mode="python",
        channels={"one"},
    )
    sandbox = ContainerSandbox(SandboxSettings(_env_file=None, profiles=[profile]))
    script = NodePython(
        source="def transform(value, context): return value",
        profile_id="python",
        profile_digest=digest(profile.model_dump(mode="json")),
    )
    assert resolve_profile(sandbox, "one", script) == profile
    assert environment_options(sandbox, "two") == []
    with pytest.raises(ServiceError, match="获准"):
        resolve_profile(sandbox, "two", script)
    with pytest.raises(ServiceError, match="变化"):
        resolve_profile(sandbox, "one", script.model_copy(update={"profile_digest": "0" * 64}))


def page(**changes):
    return {
        "items": [{"id": "one"}],
        "has_more": True,
        "cursor": "next",
        "count": 1,
        "total": 2,
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"count": 2},
        {"count": True},
        {"has_more": "true"},
        {"cursor": None},
        {"cursor": ""},
        {"items": {}},
        {"total": 0},
        {"has_more": False},
    ],
)
def test_bad_page_never_confirms_complete(changes):
    with pytest.raises(ServiceError, match="数据不完整"):
        check_page(StepPagination(count_path="count", total_path="total"), page(**changes), {}, {})


def test_repeated_cursor_and_changed_queries_are_rejected():
    policy = StepPagination(count_path="count", total_path="total")
    first = check_page(policy, page(), {"tenant_id": "one"}, {})
    assert page_arguments(policy, {"tenant_id": "one"}, first)["cursor"] == "next"
    with pytest.raises(ServiceError, match="重复"):
        check_page(policy, page(), {}, first)
    with pytest.raises(ServiceError, match="条件发生变化"):
        page_arguments(policy, {"tenant_id": "two"}, first)
    with pytest.raises(ServiceError, match="首页"):
        page_arguments(policy, {"cursor": "middle"}, {})
    last = check_page(policy, page(has_more=False), {}, first)
    assert last["complete"] is True and last["received"] == 2
    with pytest.raises(ServiceError, match="数据不完整"):
        check_page(policy.model_copy(update={"max_items": 1}), page(has_more=False), {}, first)
