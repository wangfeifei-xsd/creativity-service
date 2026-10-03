"""26 可复现入口：隔离种子、检查、真实页面、故障回归、性能与证据汇总。"""

import argparse
import hashlib
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scripts.acceptance_report import FAULTS, release_gate, schema_evidence
from scripts.render_acceptance import render

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT.parent / "creativity-web"


def command_environment():
    env = dict(os.environ)
    bundled = ROOT.parent / ".tools"
    env["PATH"] = os.pathsep.join(
        [str(bundled / "uv/bin"), str(bundled / "js/node_modules/.bin"), env["PATH"]]
    )
    env.setdefault("PLAYWRIGHT_CHANNEL", "chrome")
    return env


def source_manifest(service_root=ROOT, web_root=WEB):
    from tests.support.independence_evidence import tree

    return {
        "service_source": tree(service_root, ["src", "pyproject.toml", "uv.lock", "deploy"]),
        "web_source": tree(web_root, ["src", "package.json", "pnpm-lock.yaml", "vite.config.ts"]),
        "commits": {
            label: subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            for label, root in (("service", service_root), ("web", web_root))
        },
    }


def execute(name, command, cwd, output, env):
    print(f"执行 {name}", flush=True)
    started = datetime.now(UTC)
    with (output / "logs" / f"{name}.log").open("w") as stream:
        result = subprocess.run(
            command, cwd=cwd, env=env, stdout=stream, stderr=stream, check=False
        )
    journal_path = output / "commands.jsonl"
    with journal_path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "name": name,
                    "command": command,
                    "cwd": cwd.name,
                    "started_at": started.isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                    "exit_code": result.returncode,
                    "log": f"logs/{name}.log",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return result.returncode


def environment(output, workspace=None):
    from sqlalchemy import create_engine, text

    from creativity_service.core.config import Settings

    settings = Settings()
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.connect() as connection:
        database = {
            "version": connection.scalar(text("SHOW server_version")),
            "migration": connection.execute(
                text("SELECT version_num FROM creativity_alembic_version")
            )
            .scalars()
            .all(),
            "table_count": connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema=current_schema()"
                )
            ),
            "columns": connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema=current_schema()"
                )
            ),
            "configured_model_connections": connection.scalar(
                text("SELECT count(*) FROM model_connections")
            ),
        }
        schema_evidence(output, connection)
    engine.dispose()
    trees = {}
    for name, root in (("service", ROOT), ("web", WEB)):
        files = {}
        for base in ("src", "alembic", "deploy"):
            for path in sorted((root / base).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.name != ".DS_Store":
                    files[str(path.relative_to(root))] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
        trees[name] = {
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "files": files,
            "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        }
    hardware = {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "logical_cpus": os.cpu_count(),
    }
    if platform.system() == "Darwin":
        for key in ("hw.memsize", "machdep.cpu.brand_string"):
            hardware[key] = subprocess.check_output(["sysctl", "-n", key], text=True).strip()
    docker = subprocess.run(
        ["docker", "info", "--format", "{{json .NCPU}} {{json .MemTotal}} {{json .ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if docker.returncode == 0:
        cpus, memory, version = shlex.split(docker.stdout)
        hardware["docker"] = {"cpus": int(cpus), "memory_bytes": int(memory), "version": version}
    manifest = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "hardware": hardware,
        "database": database,
        "source": trees,
        "boundaries": {
            "database": "真实 PostgreSQL，测试使用独立 schema；迁移审查使用开发 schema",
            "redis": "真实 Redis，独立前缀",
            "mcp": "两种真实 TCP Streamable HTTP 服务，源数据由受控夹具提供",
            "model": "受控替身；未验收真实供应商",
            "worker": "组合测试调用正式 execute_message；独立 Celery 由专门故障用例验证",
            "object_store": "专门用例使用真实 MinIO，组合页面使用内存夹具",
        },
    }
    baseline = output / "onboarding/baseline.json"
    if baseline.exists():
        tested = json.loads(baseline.read_text())
        current = (
            source_manifest(workspace / "creativity-service", workspace / "creativity-web")
            if workspace
            else source_manifest()
        )
        changed = {}
        for name in ("service_source", "web_source"):
            before, after = tested[name]["files"], current[name]["files"]
            changed[name] = [
                path
                for path in sorted(before.keys() | after.keys())
                if before.get(path) != after.get(path)
            ]
        manifest["onboarding_build_still_matches_workspace"] = not any(changed.values())
        manifest["changed_since_onboarding"] = changed
        manifest["workspace_source"] = current
        manifest["verification_source"] = "独立检出" if workspace else "当前工作区"
    (output / "environment.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )


def browser(output, env):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 18006))
        except OSError as exc:
            raise RuntimeError("18006 端口已被占用，请先停止此前的临时工作区服务") from exc
    server_log = (output / "logs/workspace-server.log").open("w")
    server = subprocess.Popen(
        [sys.executable, "-m", "tests.support.workspace_server"],
        cwd=ROOT,
        env=env,
        stdout=server_log,
        stderr=server_log,
    )
    try:
        until = time.monotonic() + 30
        while time.monotonic() < until:
            if server.poll() is not None:
                raise RuntimeError("临时工作区服务启动失败，查看 workspace-server.log")
            try:
                if httpx.get("http://127.0.0.1:18006/ready", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("临时工作区服务未就绪")
        live_env = {
            **env,
            "WORKSPACE_LIVE_API": "http://127.0.0.1:18006",
            "PLAYWRIGHT_JUNIT_OUTPUT_FILE": str(output / "logs/browser.xml"),
        }
        return execute(
            "browser", ["pnpm", "test:e2e", "--reporter=list,junit"], WEB, output, live_env
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        server_log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=[
            "all",
            "checks",
            "integration",
            "faults",
            "browser",
            "onboarding",
            "performance",
            "report",
        ],
        default="all",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workspace", type=Path, help="独立检出汇总时，对比并链接原工作区根目录")
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "docs/acceptance/runs" / stamp).resolve()
    (output / "logs").mkdir(parents=True, exist_ok=True)
    env = command_environment()
    env.pop("CREATIVITY_ACCEPTANCE_PERFORMANCE", None)
    env["CREATIVITY_ACCEPTANCE_DIR"] = str(output)
    env["CREATIVITY_API_EVIDENCE_DIR"] = str(output / "api")
    env["CREATIVITY_EVALUATION_EVIDENCE_DIR"] = str(output / "evaluations")
    stages = (
        ["checks", "integration", "browser", "onboarding", "performance", "report"]
        if args.stage == "all"
        else [args.stage]
    )
    failures = []
    for stage in stages:
        commands = []
        if stage == "checks":
            checks_source = source_manifest()
            commands = [
                ("migrations", [sys.executable, "-m", "alembic", "upgrade", "head"], ROOT),
                (
                    "storage",
                    [sys.executable, "-m", "creativity_service.core.database.audit", "--database"],
                    ROOT,
                ),
                ("service-check", ["make", "check"], ROOT),
                ("web-check", ["pnpm", "check"], WEB),
            ]
        elif stage == "integration":
            commands = [
                (
                    "integration",
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "tests/integration",
                        "-q",
                        f"--junitxml={output}/logs/integration.xml",
                    ],
                    ROOT,
                )
            ]
        elif stage == "faults":
            commands = [
                (
                    "faults",
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        *[node for nodes in FAULTS.values() for node in nodes],
                        "-q",
                        f"--junitxml={output}/logs/faults.xml",
                    ],
                    ROOT,
                )
            ]
        elif stage == "browser":
            try:
                result = browser(output, env)
            except RuntimeError as exc:
                print(str(exc), flush=True)
                result = 1
            if result:
                failures.append(stage)
        elif stage == "onboarding":
            commands = [
                ("build-service", ["uv", "build", "--wheel"], ROOT),
                ("build-web", ["pnpm", "build"], WEB),
                (
                    "onboarding",
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "tests/e2e/test_acceptance.py",
                        "-q",
                        f"--junitxml={output}/logs/onboarding.xml",
                    ],
                    ROOT,
                ),
            ]
        elif stage == "performance":
            env["CREATIVITY_ACCEPTANCE_PERFORMANCE"] = "1"
            commands = [
                (
                    "performance",
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "tests/integration/acceptance/test_performance.py",
                        "-q",
                        f"--junitxml={output}/logs/performance.xml",
                    ],
                    ROOT,
                )
            ]
        elif stage == "report":
            with (output / "logs/collection.txt").open("w") as stream:
                subprocess.run(
                    [sys.executable, "-m", "pytest", "--collect-only", "-q"],
                    cwd=ROOT,
                    env=env,
                    stdout=stream,
                    check=True,
                )
            environment(output, args.workspace)
            report = render(
                output, links_root=args.workspace / "creativity-service" if args.workspace else ROOT
            )
            gate = release_gate(output, report, failures)
        for name, command, cwd in commands:
            command_env = dict(env)
            if name == "service-check":
                command_env["PYTEST_ADDOPTS"] = shlex.join(
                    [
                        *shlex.split(env.get("PYTEST_ADDOPTS", "")),
                        f"--junitxml={output}/logs/unit.xml",
                    ]
                )
            if execute(name, command, cwd, output, command_env):
                failures.append(name)
                # 构建失败不能继续把旧制品当作本次页面验收依据。
                if name.startswith("build-"):
                    break
        if stage == "checks":
            checks_source["unchanged_during_checks"] = checks_source == source_manifest()
            (output / "checks-source.json").write_text(
                json.dumps(checks_source, ensure_ascii=False, indent=2) + "\n"
            )
    print(f"证据目录：{output}", flush=True)
    if failures:
        raise SystemExit(1)
    if args.stage in {"all", "report"} and not gate["formal_cutover_allowed"]:
        print("记录已汇总；阻断范围见 release-gate.json。", flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
