"""真实 Docker 验证网络、文件、资源、输出与取消清理边界。"""

import asyncio
import json
import os
import subprocess

import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.sandbox import (
    ContainerSandbox,
    SandboxProfile,
    SandboxSettings,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def sandbox():
    image = os.getenv("CREATIVITY_SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("隔离验收需要指定本地固定摘要镜像")
    profile = SandboxProfile(
        profile_id="test",
        name="隔离验收",
        image=image,
        channels={"sandbox_test"},
        mode="python",
        command=("python3", "-I", "-B", "/work/main.py"),
        seconds=5,
        output_bytes=4096,
        memory_mb=64,
        pids=8,
    )
    return ContainerSandbox(SandboxSettings(_env_file=None, profiles=[profile])), profile


async def test_no_host_access_network_root_write_or_inherited_secret(sandbox, monkeypatch):
    runner, profile = sandbox
    monkeypatch.setenv("SANDBOX_HOST_SECRET", "must-not-enter-container")
    script = b"""import os, json, socket
denied = []
for path in ['/root/private', '/etc/sandbox-write']:
    try:
        with open(path, 'w') as f: f.write('x')
    except (OSError, PermissionError): denied.append(path)
s = socket.socket(); s.settimeout(0.2)
try: s.connect(('1.1.1.1', 443)); network = True
except OSError: network = False
print(json.dumps({'uid': os.getuid(), 'secret': os.getenv('SANDBOX_HOST_SECRET'),
 'network': network, 'denied': denied}))
"""
    result = await runner.python(profile, script, {})
    assert result == {
        "uid": 65532,
        "secret": None,
        "network": False,
        "denied": ["/root/private", "/etc/sandbox-write"],
    }


@pytest.mark.parametrize(
    "script",
    [
        b"while True: pass",
        b"print('x' * 100000)",
        b"a=bytearray(512*1024*1024)",
        b"import os,time\nwhile True:\n p=os.fork()\n if p==0: time.sleep(30)",
    ],
)
async def test_resource_limits_and_no_residual_container(sandbox, script):
    runner, profile = sandbox
    with pytest.raises((ServiceError, ExceptionGroup)):
        await runner.python(profile, script, {})
    output = await asyncio.to_thread(
        subprocess.check_output, ["docker", "ps", "-aq", "--filter", "name=creativity-sandbox-"]
    )
    assert not output.strip()


async def test_cancel_cleans_container(sandbox):
    runner, profile = sandbox
    task = asyncio.create_task(runner.python(profile, b"while True: pass", {}))
    await asyncio.sleep(1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    output = await asyncio.to_thread(
        subprocess.check_output, ["docker", "ps", "-aq", "--filter", "name=creativity-sandbox-"]
    )
    assert not output.strip()


async def test_stdio_discovery_and_call_use_same_isolation(sandbox):
    from creativity_service.core.context import Scope
    from creativity_service.core.primitives import digest
    from creativity_service.modules.mcp.schemas import McpTimeouts
    from creativity_service.modules.mcp.stdio import execute_stdio
    from creativity_service.modules.mcp.transport import McpTransport

    runner, base = sandbox
    script = """import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 method=m['method']
 if method=='initialize':
  r={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},
   'serverInfo':{'name':'隔离服务','version':'1'}}
 elif method=='tools/list':
  r={'tools':[{'name':'sum','title':'合计','inputSchema':{'type':'object','properties':{}},
   'annotations':{'readOnlyHint':True}}]}
 elif method=='tools/call': r={'content':[{'type':'text','text':'3'}]}
 else: r={}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
"""
    profile = base.model_copy(
        update={"mode": "stdio", "command": ("python3", "-I", "-u", "-c", script), "seconds": 10}
    )
    runner.settings.profiles = [profile]

    async def operation(session, handshake, bounds):
        tools = await McpTransport.list_tools(session, handshake)
        assert tools[0].title == "合计"
        result = await session.call_tool("sum", {})
        return json.loads(result.model_dump_json())

    result = await execute_stdio(
        runner,
        Scope(channel_id="sandbox_test", environment="test"),
        f"sandbox://test/{digest(profile.model_dump(mode='json'))}",
        None,
        McpTimeouts(),
        operation,
    )
    assert result["content"][0]["text"] == "3"


async def test_stdio_invalid_output_fails_before_operation_timeout(sandbox):
    from time import monotonic

    from creativity_service.core.context import Scope
    from creativity_service.core.primitives import digest
    from creativity_service.modules.mcp.schemas import McpTimeouts
    from creativity_service.modules.mcp.stdio import execute_stdio

    runner, base = sandbox
    profile = base.model_copy(
        update={
            "mode": "stdio",
            "seconds": 20,
            "command": (
                "python3",
                "-u",
                "-c",
                "import time;print('invalid-json',flush=True);time.sleep(20)",
            ),
        }
    )
    runner.settings.profiles = [profile]

    async def unused(*args):
        raise AssertionError("无效初始化不能进入业务调用")

    started = monotonic()
    with pytest.raises(ServiceError) as error:
        await execute_stdio(
            runner,
            Scope(channel_id="sandbox_test", environment="test"),
            f"sandbox://test/{digest(profile.model_dump(mode='json'))}",
            None,
            McpTimeouts(),
            unused,
        )
    assert error.value.code == "MCP_RESULT_INVALID"
    assert monotonic() - started < 10


async def test_watchdog_reaps_expired_owned_container(sandbox):
    runner, profile = sandbox
    name = "creativity-sandbox-watchdog-test"
    await asyncio.to_thread(
        subprocess.check_call,
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label=creativity.sandbox=1",
            "--label=creativity.deadline=1",
            "--network=none",
            "--entrypoint",
            "python3",
            profile.image,
            "-c",
            "import time;time.sleep(60)",
        ],
        stdout=subprocess.DEVNULL,
    )
    try:
        await runner.reap()
        output = await asyncio.to_thread(
            subprocess.check_output, ["docker", "ps", "-aq", "--filter", "name=" + name]
        )
        assert not output.strip()
    finally:
        await runner.cleanup(name)
