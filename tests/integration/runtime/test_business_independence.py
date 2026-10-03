"""23：固定平台制品下，由真实管理页面接入两类工具及第三渠道。"""

import asyncio
import base64
import copy
import json
import os
from contextlib import AsyncExitStack, ExitStack
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.schemas import (
    AgentCreate,
    AgentValidateInput,
    AgentVersionEdit,
)
from creativity_service.modules.skills.schemas import (
    SkillFileInput,
    SkillSettings,
    SkillVersionCreate,
    SkillVersionEdit,
)
from creativity_service.modules.tools.schemas import ToolCreate, ToolRelease, ToolVersionCreate
from examples.backend.client import BusinessBackendClient, PlatformError, Principal
from examples.mcp.server import serve
from examples.onboarding.prepare import prepare
from tests.integration.agents.test_agents import publish
from tests.integration.channels.conftest import channel_env as channel_env
from tests.support.independence_evidence import ROOT, snapshot
from tests.support.independence_runtime import allow_source, browser, model_config, runtime

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("CREATIVITY_INDEPENDENCE_EVIDENCE_DIR"),
        reason="固定构建浏览器验收须通过 scripts/verify_business_independence.py 显式启动",
    ),
]


class Principals:
    def __init__(self, scenario):
        self.value = Principal(
            "member",
            "shared-user-001",
            {"type": scenario["scope_type"], "id": scenario["scope_id"]},
            ["run:create", "run:read", "run:content", "data:read_sensitive"],
            {
                kind: ["*"]
                for kind in ("agent", "model", "model_route", "prompt", "skill", "tool", "run")
            },
        )

    async def current(self):
        return self.value


def write(folder, name, data):
    (folder / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


async def answer_from_tool(env, context, attempt):
    request = env.adapter.calls[-1][1]
    contents = json.loads(request.messages[-1]["content"])
    result = contents["tool_results"]["lookup"]
    env.adapter.responses = [
        {
            "business_status": "COMPLETED",
            "schema_version": "1.0",
            "data": result["data"],
            "warnings": [],
            "evidence_refs": result["evidence_refs"],
        }
    ]


async def run_case(env, item, suffix, delivery="stream", expected="SUCCEEDED"):
    client, scenario = item.backend, item.scenario
    record = await client.submit("shared_agent", scenario["input"], suffix, delivery)
    run = await client.wait(record["run_id"], wait_seconds=120, poll_interval=2)
    assert run["state"] == expected, run["error"]
    return run


async def successful(env, item, folder):
    record = await run_case(env, item, "same-key-across-channels")
    assert record["result"]["data"] == item.scenario["data"]
    jsonschema.validate(record["result"], item.definition["output_schema"])
    events = [event async for event in item.backend.subscribe(record["run_id"])]
    assert [event.sequence for event in events] == sorted({event.sequence for event in events})
    assert events[-1].type == "completed"
    assert len([e for e in events if e.type == "result"]) == 1
    assert [e for e in events if e.type == "result"][0].data["payload"] == record["result"]
    assert any(e.type == "text_delta" and e.data["payload"]["validated"] is False for e in events)
    cursor = events[1].sequence
    resumed = [e async for e in item.backend.subscribe(record["run_id"], cursor)]
    assert [e.sequence for e in resumed] == [e.sequence for e in events if e.sequence > cursor]
    row = await env.runs.load(
        TaskEnvelope(channel_id=item.context.scope.channel_id, run_id=record["run_id"])
    )
    calls = await env.tools.management.repository.rows(
        env.runs.context(row), "tool_calls", run_id=record["run_id"]
    )
    assert len(calls) == 1 and calls[0]["source_request_id"]
    refs = record["result"]["evidence_refs"]
    assert refs and all(ref["scope"]["channel_id"] == item.context.scope.channel_id for ref in refs)
    usage = record["usage_summary"]
    assert usage["input_tokens"] == 10 and usage["output_tokens"] == 20
    assert usage["complete"] and usage["unpriced_count"] == 1 and usage["amounts"] == {}
    invalid = await expect_denied(
        lambda: item.backend.submit("shared_agent", {"unknown": True}, "invalid-input"), (422,)
    )
    repeat = await item.backend.submit(
        "shared_agent", item.scenario["input"], "same-key-across-channels", "sync"
    )
    assert repeat["run_id"] == record["run_id"] and repeat["result"] == record["result"]
    protocol = {entry["method"] for entry in item.source.protocol_calls}
    assert {"initialize", "notifications/initialized", "tools/list", "tools/call"} <= protocol
    sent = [c for c in item.source.calls if c["name"] == item.source.tool_name]
    assert sent and sent[-1]["arguments"] == item.scenario["input"]
    assert sent[-1]["_meta"]["creativity.identity"]["subject_id"] == "shared-user-001"
    assert sent[-1]["_meta"]["creativity.identity"]["channel_id"] == record["channel_id"]
    evidence = {
        "scenario": item.scenario,
        "invalid_input": invalid,
        "run": record,
        "events": [{"type": e.type, "sequence": e.sequence, "data": e.data} for e in events],
        "resume_after": cursor,
        "resumed_sequences": [e.sequence for e in resumed],
        "source_calls": [
            {key: c[key] for key in ("id", "run_id", "source_request_id", "state")} for c in calls
        ],
        "protocol": item.source.protocol_calls,
        "http": item.http,
        "configuration": item.config,
    }
    write(folder, item.scenario["code"] + ".json", evidence)
    item.run = record
    return evidence


async def expect_denied(call, status=(403, 404, 409)):
    with pytest.raises(PlatformError) as error:
        await call()
    assert error.value.status in status
    assert error.value.request_id
    return {
        "status": error.value.status,
        "code": error.value.code,
        "request_id": error.value.request_id,
    }


async def same_local_code(env, items):
    records = []
    for item in items:
        context = item.context
        original = await env.tools.management.version_detail(
            context, item.resources["imported"]["imported_version"]
        )
        # 通用管理服务允许同一 MCP 绑定建立本地别名；两个渠道使用完全相同编码。
        tool = await env.tools.management.create(
            context,
            ToolCreate(
                tool_code="shared_lookup",
                name="同编码隔离工具",
                description="验证渠道内编码解析",
                source_type="mcp",
                owner="接入验证人员",
            ),
        )
        version = await env.tools.management.create_version(
            context,
            tool.tool_id,
            ToolVersionCreate(
                version_label="隔离验证",
                definition=original.definition,
            ),
        )
        frozen = await env.tools.management.freeze(
            context, version.version.version_id, version.revision
        )
        await env.tools.management.release(
            context, tool.tool_id, ToolRelease(version_id=frozen.version.version_id)
        )
        spec = await env.agents.resolve_published(context, "shared_agent")
        definition = spec.definition.model_copy(
            update={
                "bindings": spec.definition.bindings.model_copy(
                    update={
                        "tool_versions": (frozen.version.version_id,),
                        "skill_versions": (),
                        "skill_loading": (),
                    }
                ),
                "steps": tuple(
                    s.model_copy(update={"dependency": frozen.version.version_id})
                    if s.key == "lookup"
                    else s
                    for s in spec.definition.steps
                ),
            }
        )
        agent = await env.agents.create(
            context,
            AgentCreate(
                agent_code="same_code_probe",
                name="同编码隔离验证",
                description="使用本渠道同名工具",
                owner="接入验证人员",
                definition=definition,
            ),
        )
        await publish(SimpleNamespace(agents=env.agents, context=context), agent)
        receipt = await item.backend.submit("same_code_probe", item.scenario["input"], "same-code")
        run = await item.backend.wait(receipt["run_id"], wait_seconds=120, poll_interval=2)
        assert run["state"] == "SUCCEEDED", run["error"]
        assert run["result"]["data"] == item.scenario["data"]
        records.append(
            {
                "channel_id": context.scope.channel_id,
                "tool": tool.model_dump(mode="json"),
                "run": run,
            }
        )
    assert len({r["tool"]["tool_id"] for r in records}) == len(items)
    return records


async def test_fixed_build_two_businesses_and_third_channel_via_management_pages(
    channel_env, tmp_path
):
    folder = await asyncio.to_thread(
        Path(os.environ.get("CREATIVITY_INDEPENDENCE_EVIDENCE_DIR", str(tmp_path))).resolve
    )
    folder.mkdir(parents=True, exist_ok=True)
    scenarios = json.loads((ROOT / "examples/onboarding/scenarios.json").read_text())
    evidence = {
        "verification": {
            "data": "controlled_fixture",
            "model": "controlled_fixture",
            "mcp": "real_tcp",
            "api": "real_tcp",
            "ui": "Playwright against built web and real management HTTP",
            "database": "PostgreSQL isolated schema",
            "auth": "Redis isolated prefix",
            "worker": "execute_message_in_process",
            "object_store": "memory_fixture",
            "production_released": False,
            "test_scope_retained": False,
        },
        "scenarios": [],
        "boundaries": {},
    }
    async with runtime(channel_env, folder) as env, AsyncExitStack() as clients:
        with ExitStack() as sources:
            baseline = await snapshot(env)
            write(folder, "baseline.json", baseline)
            items = []
            for scenario in scenarios:
                if len(items) == 2:
                    after_two = await snapshot(env)
                    assert after_two == baseline
                    write(folder, "after-two.json", after_two)
                env.worker_enabled = False
                source = sources.enter_context(serve(scenario["profile"]))
                source.data = copy.deepcopy(scenario["data"])
                source.input_schema = copy.deepcopy(
                    scenario.get("input_schema", source.input_schema)
                )
                account = await browser(env, "channel", scenario)
                manager = await env.iam.authentication.admin_session(
                    account["auth"]["access_token"], "verification-23", governance=True
                )
                context = manager.context
                tenant = SimpleNamespace(manager=manager, token=account["auth"]["access_token"])
                allow_source(env, context.scope.channel_id, source)
                await model_config(env, tenant)
                source.scope = context.scope.model_dump()
                source.permissions[context.scope.subject_id] = {
                    "actions": ["run:create", "run:read", "run:content", "data:read_sensitive"],
                    "resources": {
                        kind: ["*"] for kind in ("agent", "tool", "model", "prompt", "skill", "run")
                    },
                }
                archive, definition = prepare(source, folder / scenario["code"])
                resources = await browser(
                    env,
                    "resources",
                    scenario,
                    endpoint=source.endpoint,
                    remoteTool=source.tool_name,
                    skillPackage=str(archive),
                )
                tool = resources["imported"]
                definition["steps"][0]["dependency"] = tool["imported_version"]

                async def model_response(context, attempt):
                    await answer_from_tool(env, context, attempt)

                env.adapter.before = model_response
                env.worker_enabled = True
                agent = await browser(
                    env,
                    "agent",
                    scenario,
                    definition=definition,
                    screenshot=str(folder / f"{scenario['code']}-published.png"),
                )
                debug = await env.runs.get_run(context, agent["debug"]["run_id"])
                assert debug.state == "FAILED" and debug.error.code == "TOOL_FORBIDDEN"
                agent["debug"] = debug.model_dump(mode="json")
                assert not any(c["name"] == source.tool_name for c in source.calls)
                navigation = await env.iam.sessions.view(manager)
                assert not {"matching", "risk", "analysis"} & {
                    n.navigation_key for n in navigation.navigation
                }
                principal = Principals(scenario)
                source.scope = context.scope.model_copy(
                    update={"subject_type": "member", "subject_id": principal.value.subject_id}
                ).model_dump()
                source.permissions = {
                    principal.value.subject_id: {
                        "actions": principal.value.actions,
                        "resources": principal.value.resources,
                    }
                }
                backend = await clients.enter_async_context(
                    BusinessBackendClient(
                        env.api_url,
                        account["key"]["api_key"],
                        account["delegation"]["key"]["kid"],
                        base64.b64decode(account["delegation"]["signing_secret"]),
                        "controlled-source",
                        "creativity-api",
                        principal,
                    )
                )
                item = SimpleNamespace(
                    scenario=scenario,
                    context=context,
                    source=source,
                    backend=backend,
                    principal=principal,
                    definition=definition,
                    resources=resources,
                    agent=agent,
                    http=[],
                    config={
                        "channel": account["channel"],
                        "client": account["client"],
                        "key_id": account["key"]["key"]["key_id"],
                        "delegation_kid": account["delegation"]["key"]["kid"],
                        "mcp": resources["connection"],
                        "discovery": resources["discovery"],
                        "tool": tool,
                        "skill": resources["frozenSkill"],
                        "agent": agent["release"],
                        "validation": agent["validation"],
                        "debug": agent["debug"],
                        "navigation": [n.model_dump(mode="json") for n in navigation.navigation],
                        "ui_requests": account["requests"]
                        + resources["requests"]
                        + agent["requests"],
                    },
                )

                async def record(response, target=item):
                    target.http.append(
                        {
                            "method": response.request.method,
                            "path": response.request.url.path,
                            "status": response.status_code,
                            "request_id": response.headers.get("X-Request-ID"),
                        }
                    )

                backend.http.event_hooks["response"] = [record]
                evidence["scenarios"].append(await successful(env, item, folder))
                items.append(item)
            assert not env.runtime.registry.handlers
            after_three = await snapshot(env)
            assert after_three == baseline
            write(folder, "after-three.json", after_three)
            assert len({item.run["run_id"] for item in items}) == 3
            assert items[0].source.tool_name == items[2].source.tool_name
            assert items[0].run["result"]["data"] != items[2].run["result"]["data"]
            boundary = evidence["boundaries"]
            boundary["same_local_tool_code"] = await same_local_code(env, items)
            boundary["foreign_run"] = await expect_denied(
                lambda: items[2].backend.query(items[0].run["run_id"])
            )

            async def foreign_events():
                return [e async for e in items[2].backend.subscribe(items[0].run["run_id"])]

            boundary["foreign_events"] = await expect_denied(foreign_events)
            # 冻结技能不可原地改写；新内容只能形成独立候选版本。
            skill = items[0].resources["frozenSkill"]
            frozen_id = skill["version_id"]
            _, raw = await env.skills.raw(items[0].context, frozen_id)
            settings = SkillSettings.model_validate(
                {
                    key: value
                    for key, value in raw["content"].items()
                    if key in SkillSettings.model_fields
                }
            )
            with pytest.raises(ServiceError) as frozen:
                await env.skills.edit_version(
                    items[0].context,
                    frozen_id,
                    SkillVersionEdit(
                        revision=skill["revision"],
                        settings=settings,
                        files=(
                            SkillFileInput(
                                relative_path="references/output.md", text="未经发布的变化"
                            ),
                        ),
                    ),
                )
            assert frozen.value.code == "VERSION_FROZEN"
            new = await env.skills.create_version(
                items[0].context,
                items[0].resources["skill"]["skill"]["skill_id"],
                SkillVersionCreate(
                    version_label="变更验证",
                    base_version_id=frozen_id,
                ),
            )
            changed = await env.skills.edit_version(
                items[0].context,
                new.version_id,
                SkillVersionEdit(
                    revision=new.revision,
                    settings=settings,
                    files=(
                        SkillFileInput(
                            relative_path="references/output.md",
                            text="新规则：输出须附带审核说明。\n",
                        ),
                    ),
                ),
            )
            assert changed.package_hash != skill["package_hash"]
            old = await run_case(env, items[0], "after-skill-change", "sync")
            published = await env.agents.resolve_published(items[0].context, "shared_agent")
            assert published.source_version_id == items[0].agent["release"]["release_version_id"]
            assert published.definition.bindings.skill_versions == (frozen_id,)
            assert old["result"]["data"] == items[0].scenario["data"]
            # 更新 Agent 草稿引用后，旧修订和旧摘要都不能作为新候选使用。
            frozen_new = await env.skills.freeze(
                items[0].context, changed.version_id, changed.revision
            )
            detail = await env.agents.detail(
                items[0].context, items[0].agent["agent"]["agent"]["agent_id"]
            )
            draft = next(v for v in detail.versions if v.status.value == "DRAFT")
            definition = draft.definition.model_copy(deep=True)
            definition = definition.model_copy(
                update={
                    "bindings": definition.bindings.model_copy(
                        update={
                            "skill_versions": (frozen_new.version_id,),
                            "skill_loading": tuple(
                                b.model_copy(update={"version_id": frozen_new.version_id})
                                for b in definition.bindings.skill_loading
                            ),
                        }
                    ),
                }
            )
            edited = await env.agents.edit_version(
                items[0].context,
                draft.version_id,
                AgentVersionEdit(revision=draft.revision, definition=definition),
            )
            with pytest.raises(ServiceError) as stale:
                await env.agents.validate(
                    items[0].context, draft.version_id, AgentValidateInput(revision=draft.revision)
                )
            assert stale.value.code == "REVISION_CONFLICT"
            current = await env.agents.validate(
                items[0].context, draft.version_id, AgentValidateInput(revision=edited.revision)
            )
            assert (
                current.valid
                and current.content_digest != items[0].agent["validation"]["content_digest"]
            )
            boundary["skill_version"] = {
                "frozen_edit_error": frozen.value.code,
                "stale_revision_error": stale.value.code,
                "old_package_hash": skill["package_hash"],
                "new_package_hash": changed.package_hash,
                "old_candidate_digest": items[0].agent["validation"]["content_digest"],
                "new_candidate_digest": current.content_digest,
                "published_mapping_unchanged": True,
                "retry_run_id": old["run_id"],
            }
            # 先排队，再改变源契约，验证运行边界而非仅检查管理表单。
            env.worker_enabled = False
            queued = await items[1].backend.submit(
                "shared_agent", items[1].scenario["input"], "changed-schema"
            )
            before_schema_calls = sum(
                c["name"] == items[1].source.tool_name for c in items[1].source.calls
            )
            items[1].source.schema_revision = 2
            env.worker_enabled = True
            failed = await items[1].backend.wait(
                queued["run_id"], wait_seconds=120, poll_interval=2
            )
            assert failed["state"] == "FAILED", failed
            assert failed["error"]["code"] in {"MCP_TOOL_CHANGED", "SUBJECT_REVIEW_CHANGED"}
            assert (
                sum(c["name"] == items[1].source.tool_name for c in items[1].source.calls)
                == before_schema_calls
            )
            boundary["source_schema"] = {"run": failed, "business_tool_dispatched": False}
            # 同号主体在第一渠道撤权，不影响第三渠道。
            env.worker_enabled = False
            queued = await items[0].backend.submit(
                "shared_agent", items[0].scenario["input"], "revoke-before-worker"
            )
            before_calls = len(
                [c for c in items[0].source.calls if c["name"] == items[0].source.tool_name]
            )
            items[0].source.permissions = {}
            env.worker_enabled = True

            async with asyncio.timeout(30):
                while True:
                    failed_revoke = await env.runs.load(
                        TaskEnvelope(
                            channel_id=items[0].context.scope.channel_id, run_id=queued["run_id"]
                        )
                    )
                    if failed_revoke["state"] in {"FAILED", "CANCELLED"}:
                        break
                    await asyncio.sleep(0.05)
            assert (
                len([c for c in items[0].source.calls if c["name"] == items[0].source.tool_name])
                == before_calls
            )
            boundary["revoked_query"] = await expect_denied(
                lambda: items[0].backend.query(items[0].run["run_id"])
            )
            boundary["revoked_idempotency"] = await expect_denied(
                lambda: items[0].backend.submit(
                    "shared_agent", items[0].scenario["input"], "same-key-across-channels"
                )
            )

            async def revoked_events():
                return [e async for e in items[0].backend.subscribe(items[0].run["run_id"])]

            boundary["revoked_events"] = await expect_denied(revoked_events)
            boundary["revoked_worker"] = {
                "run_id": failed_revoke["id"],
                "state": failed_revoke["state"],
                "error": failed_revoke["error"],
                "business_tool_dispatched": False,
            }
            isolated = await run_case(env, items[2], "other-channel-still-authorized")
            boundary["other_channel_after_revocation"] = isolated
            assert isolated["result"]["data"] == items[2].scenario["data"]
            final = await snapshot(env)
            assert final == baseline
            write(folder, "final.json", final)
            evidence["build_comparison"] = {
                "stages": ["baseline", "after-two", "after-three", "final"],
                "all_equal": True,
                "sha256": {
                    key: value["sha256"]
                    for key, value in baseline.items()
                    if isinstance(value, dict) and "sha256" in value
                },
                "migration_versions": baseline["migration_versions"],
            }
            write(folder, "evidence.json", evidence)
