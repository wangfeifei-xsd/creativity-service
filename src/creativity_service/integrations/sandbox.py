"""固定镜像的无网络容器执行边界；宿主机仅启动 Docker 客户端。"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import time
from typing import Any
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import Contract, Identifier, ServiceError, canonical_json


class SandboxProfile(Contract):
    profile_id: Identifier
    name: str = Field(min_length=1, max_length=128)
    image: str = Field(pattern=r"^(?:[a-zA-Z0-9._:/-]+@)?sha256:[0-9a-f]{64}$")
    command: tuple[str, ...] = Field(min_length=1, max_length=32)
    channels: frozenset[str] = Field(min_length=1)
    mode: str = Field(pattern=r"^(python|stdio)$")
    cpu: float = Field(default=1, ge=0.1, le=4)
    memory_mb: int = Field(default=128, ge=32, le=1024)
    pids: int = Field(default=32, ge=8, le=128)
    seconds: int = Field(default=30, ge=1, le=120)
    input_bytes: int = Field(default=1048576, ge=1024, le=2097152)
    output_bytes: int = Field(default=262144, ge=1024, le=2097152)
    credential_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_SANDBOX_", env_file=".env", extra="ignore"
    )
    docker: str = "docker"
    profiles: list[SandboxProfile] = []


class ContainerSandbox:
    def __init__(self, settings: SandboxSettings | None = None) -> None:
        self.settings = settings or SandboxSettings()

    def profile(self, channel_id: str, identifier: str, mode: str) -> SandboxProfile:
        value = next(
            (
                p
                for p in self.settings.profiles
                if p.profile_id == identifier and p.mode == mode and channel_id in p.channels
            ),
            None,
        )
        if value is None:
            raise ServiceError("SANDBOX_UNAVAILABLE", "当前渠道没有获准的隔离执行环境", 503)
        return value

    def command(self, profile: SandboxProfile, name: str, workdir: str | None = None) -> list[str]:
        args = [
            self.settings.docker,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--label=creativity.sandbox=1",
            f"--label=creativity.deadline={int(time()) + profile.seconds + 10}",
            "--network=none",
            "--user=65532:65532",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={profile.pids}",
            f"--memory={profile.memory_mb}m",
            f"--memory-swap={profile.memory_mb}m",
            f"--cpus={profile.cpu}",
            "--ulimit=nofile=64:64",
            "--ulimit=fsize=16777216:16777216",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--stop-timeout=1",
            "-i",
        ]
        if workdir:
            args.extend(
                ["--mount", f"type=bind,source={workdir},target=/work,readonly", "--workdir=/work"]
            )
        if profile.credential_env:
            args.extend(["--env", profile.credential_env])
        return [*args, "--entrypoint", profile.command[0], profile.image, *profile.command[1:]]

    async def cleanup(self, name: str) -> None:
        process = await asyncio.create_subprocess_exec(
            self.settings.docker,
            "rm",
            "-f",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(10):
                _, error = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise ServiceError("SANDBOX_CLEANUP_FAILED", "隔离执行清理未完成", 503) from None
        if process.returncode and b"No such container" not in error:
            raise ServiceError("SANDBOX_CLEANUP_FAILED", "隔离执行清理失败，请检查容器服务", 503)

    async def reap(self) -> None:
        """仅回收平台标记且超过绝对截止时间的容器，处理 Worker 意外终止。"""
        if not self.settings.profiles:
            return
        assert_external_io_allowed()
        process = await asyncio.create_subprocess_exec(
            self.settings.docker,
            "ps",
            "-a",
            "--filter",
            "label=creativity.sandbox=1",
            "--format",
            '{{.ID}} {{.Label "creativity.deadline"}}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(10):
                output, _ = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise ServiceError("SANDBOX_CLEANUP_FAILED", "隔离容器回收检查超时", 503) from None
        if process.returncode:
            raise ServiceError("SANDBOX_CLEANUP_FAILED", "无法检查隔离容器", 503)
        for line in output.decode().splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].isdigit() and int(fields[1]) <= time():
                await self.cleanup(fields[0])

    @asynccontextmanager
    async def process(
        self, profile: SandboxProfile, *, directory: str | None = None, token: str | None = None
    ) -> AsyncIterator[asyncio.subprocess.Process]:
        assert_external_io_allowed()
        name = "creativity-sandbox-" + uuid4().hex
        # Docker 客户端保留连接配置；只有明确声明的凭据环境变量传入容器。
        environment = dict(os.environ)
        if profile.credential_env:
            environment[profile.credential_env] = token or ""
        process = await asyncio.create_subprocess_exec(
            *self.command(profile, name, directory),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            limit=profile.output_bytes + 1,
        )
        try:
            async with asyncio.timeout(profile.seconds):
                yield process
        except TimeoutError:
            raise ServiceError("SANDBOX_TIMEOUT", "隔离执行超过时限", 504) from None
        finally:
            try:
                await asyncio.shield(self.cleanup(name))
            finally:
                if process.returncode is None:
                    process.kill()
                # 停止后排空有界管道，避免回压阻止进程退出。
                async with asyncio.timeout(10):
                    await process.communicate()

    @staticmethod
    async def read(stream: asyncio.StreamReader, maximum: int) -> bytes:
        data = bytearray()
        while block := await stream.read(min(65536, maximum + 1)):
            data.extend(block)
            if len(data) > maximum:
                raise ServiceError("SANDBOX_OUTPUT_LIMIT", "隔离执行输出超过上限", 502)
        return bytes(data)

    async def python(self, profile: SandboxProfile, script: bytes, values: dict[str, Any]) -> Any:
        try:
            payload = canonical_json({"script": script.decode("utf-8"), "input": values})
        except UnicodeError:
            raise ServiceError("SANDBOX_SCRIPT_INVALID", "脚本须使用 UTF-8 编码", 422) from None
        if profile.mode != "python" or len(payload) > profile.input_bytes:
            raise ServiceError("SANDBOX_INPUT_LIMIT", "隔离执行输入超过上限", 422)
        if profile.credential_env:
            raise ServiceError("SANDBOX_PROFILE_INVALID", "计算脚本不能注入服务凭据", 422)
        if profile.command not in {
            ("python3", "-I", "-B"),
            ("python3", "-I", "-B", "/work/main.py"),
        }:
            raise ServiceError(
                "SANDBOX_PROFILE_INVALID", "Python 环境须使用固定隔离解释器入口", 422
            )
        # 源码与输入经标准输入进入容器，避免依赖宿主目录共享或挂载主机文件。
        bootstrap = (
            "import sys,json,io\n"
            "p=json.load(sys.stdin)\n"
            "sys.stdin=io.TextIOWrapper(io.BytesIO(json.dumps(p['input']).encode()),encoding='utf-8')\n"
            "exec(compile(p['script'],'/work/main.py','exec'),{'__name__':'__main__'})\n"
        )
        execution = profile.model_copy(update={"command": ("python3", "-I", "-B", "-c", bootstrap)})
        try:
            async with self.process(execution) as process:
                assert process.stdin and process.stdout and process.stderr

                async def send() -> None:
                    assert process.stdin
                    process.stdin.write(payload)
                    await process.stdin.drain()
                    process.stdin.close()

                async with asyncio.TaskGroup() as group:
                    group.create_task(send())
                    output = group.create_task(self.read(process.stdout, profile.output_bytes))
                    group.create_task(self.read(process.stderr, profile.output_bytes))
                    group.create_task(process.wait())
                if process.returncode:
                    raise ServiceError("SANDBOX_EXIT_FAILED", "隔离脚本退出异常或资源超过上限", 502)
                try:
                    return json.loads(output.result())
                except (ValueError, UnicodeError):
                    raise ServiceError(
                        "SANDBOX_RESULT_INVALID", "隔离脚本须输出一个 JSON 结果", 502
                    ) from None
        except ExceptionGroup as exc:

            def known(error: BaseException) -> ServiceError | None:
                if isinstance(error, ServiceError):
                    return error
                if isinstance(error, BaseExceptionGroup):
                    return next(
                        (found for child in error.exceptions if (found := known(child))), None
                    )
                return None

            raise known(exc) or ServiceError(
                "SANDBOX_EXIT_FAILED", "隔离脚本退出异常", 502
            ) from None
