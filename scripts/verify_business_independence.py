"""在固定提交的独立检出中构建并执行 23，防止其他单元的在途改动污染证据。"""

import argparse
import hashlib
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

SERVICE_FILES = [
    "examples/mcp/server.py",
    "examples/onboarding",
    "tests/support/independence_evidence.py",
    "tests/support/independence_runtime.py",
    "tests/integration/runtime/test_business_independence.py",
]
WEB_DRIVER = "tests/support/business-independence.mjs"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-ref", default="a28b59f")
    parser.add_argument("--web-ref", default="305a077")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checks", action="store_true", help="构建后、固定验收前运行两工程检查")
    args = parser.parse_args()
    service = Path(__file__).resolve().parents[1]
    web = service.parent / "creativity-web"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or service / ".logs" / ("23-replay-" + stamp)).resolve()
    output.mkdir(parents=True, exist_ok=False)
    tools = service.parent / ".tools"
    uv = tools / "uv/bin/uv"
    uv = str(uv) if uv.exists() else shutil.which("uv")
    node = tools / "js/node_modules/node/bin/node"
    node = str(node) if node.exists() else shutil.which("node")
    pnpm = tools / "js/node_modules/pnpm/bin/pnpm.cjs"
    pnpm = [node, str(pnpm)] if pnpm.exists() else [shutil.which("pnpm")]
    if not uv or not node or not pnpm[0]:
        parser.error("需要 uv 0.10.12、Node.js 22 与 pnpm 10.32.1")
    env = {**os.environ, "CREATIVITY_TEST_NODE": node}
    env["PATH"] = os.pathsep.join([str(Path(uv).parent), str(Path(node).parent), env["PATH"]])
    if tools.exists():
        (output / ".tools").symlink_to(tools, target_is_directory=True)
        env["PATH"] = str(tools / "js/node_modules/.bin") + os.pathsep + env["PATH"]

    def run(command, cwd, log):
        print(f"执行：{log}", flush=True)
        with (output / log).open("w") as stream:
            subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=stream, check=True)

    for repo, ref in ((service, args.service_ref), (web, args.web_ref)):
        target = output / repo.name
        run(
            ["git", "worktree", "add", "--detach", str(target), ref],
            repo,
            repo.name + "-checkout.log",
        )
    target_service, target_web = output / service.name, output / web.name
    for name in SERVICE_FILES:
        src, dst = service / name, target_service / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(
                src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__")
            )
        else:
            shutil.copy2(src, dst)
    (target_web / WEB_DRIVER).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(web / WEB_DRIVER, target_web / WEB_DRIVER)
    if (service / ".env").exists():
        shutil.copy2(service / ".env", target_service / ".env")
        (target_service / ".env").chmod(0o600)
    run([uv, "sync", "--locked"], target_service, "install-service.log")
    original_lock = (web / "pnpm-lock.yaml").read_bytes()
    target_lock = (target_web / "pnpm-lock.yaml").read_bytes()
    if (
        hashlib.sha256(original_lock).digest() == hashlib.sha256(target_lock).digest()
        and (web / "node_modules").is_dir()
    ):
        (target_web / "node_modules").symlink_to(web / "node_modules", target_is_directory=True)
    else:
        run([*pnpm, "install", "--frozen-lockfile"], target_web, "install-web.log")
    run([uv, "build", "--wheel"], target_service, "build-service.log")
    run([*pnpm, "build"], target_web, "build-web.log")
    if args.checks:
        run(["make", "check"], target_service, "check-service.log")
        run([*pnpm, "check"], target_web, "check-web.log")
    env["CREATIVITY_INDEPENDENCE_EVIDENCE_DIR"] = str(output / "evidence")
    run(
        [uv, "run", "pytest", "tests/integration/runtime/test_business_independence.py", "-q"],
        target_service,
        "acceptance.log",
    )
    print(f"验证证据：{output / 'evidence/evidence.json'}", flush=True)
    print("已保留独立检出和脱敏证据；测试数据库、Redis 前缀与临时服务已清理。", flush=True)


if __name__ == "__main__":
    main()
