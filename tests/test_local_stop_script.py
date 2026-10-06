"""在隔离目录验证终止脚本兼容旧启动器，不触碰实际开发服务。"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def local_project(tmp_path):
    root = tmp_path / "后端 项目"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/stop-local.sh", scripts)
    processes = []
    yield root, processes
    for process in processes:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def stop(root, *args):
    return subprocess.run(
        [str(root / "scripts/stop-local.sh"), *args],
        cwd=root.parent,
        capture_output=True,
        text=True,
        timeout=40,
    )


def start_launcher(root, processes):
    module = root / "src/creativity_service/development/launcher.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    # 保持旧启动器的空锁文件和进程组退出协议，不要求新增 PID 状态。
    module.write_text(
        """
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

state = Path(".local/development")
state.mkdir(parents=True, exist_ok=True)
lock = (state / "start.lock").open("a+")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
children = [
    subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    for _ in range(3)
]
(state / "children").write_text(" ".join(str(child.pid) for child in children))

def interrupt(*_args):
    raise KeyboardInterrupt

signal.signal(signal.SIGTERM, interrupt)
(state / "ready").touch()
try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    pass
finally:
    for child in children:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
    lock.close()
"""
    )
    # 禁用 site 初始化，避免虚拟环境的可编辑安装覆盖隔离测试模块。
    process = subprocess.Popen(
        [
            sys.executable,
            "-S",
            "-m",
            "creativity_service.development.launcher",
            "--port",
            "18001",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    processes.append(process)
    deadline = time.monotonic() + 5
    ready = root / ".local/development/ready"
    while not ready.exists() and time.monotonic() < deadline:
        assert process.poll() is None
        time.sleep(0.02)
    assert ready.exists()
    return process


def test_stop_cleans_old_launcher_children_keeps_lock_and_can_repeat(local_project):
    root, processes = local_project
    process = start_launcher(root, processes)
    state = root / ".local/development"
    child_pids = [int(pid) for pid in (state / "children").read_text().split()]
    lock_inode = (state / "start.lock").stat().st_ino
    result = stop(root)
    assert result.returncode == 0, result.stderr
    assert "API、Worker 和调度器已停止" in result.stdout
    assert "继续保留" in result.stdout
    process.wait(timeout=5)
    assert process.returncode == 0
    for pid in child_pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert (state / "start.lock").stat().st_ino == lock_inode
    assert "未运行" in stop(root).stdout

    # 不删除锁文件也能重新启动，避免通过更换 inode 绕过互斥。
    (state / "ready").unlink()
    restarted = start_launcher(root, processes)
    assert stop(root).returncode == 0
    restarted.wait(timeout=5)


def test_stop_does_not_kill_unrelated_holder_or_other_project(local_project):
    root, processes = local_project
    state = root / ".local/development"
    state.mkdir(parents=True)
    unrelated = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import time; "
            "lock = Path('.local/development/start.lock').open('a+'); "
            "Path('.local/development/ready').touch(); time.sleep(120)",
        ],
        cwd=root,
    )
    processes.append(unrelated)
    deadline = time.monotonic() + 5
    while not (state / "ready").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (state / "ready").exists()
    other_root = root.parent / "其他项目"
    other_root.mkdir()
    other = start_launcher(other_root, processes)
    result = stop(root)
    assert result.returncode == 0
    assert "未运行" in result.stdout
    assert unrelated.poll() is None
    assert other.poll() is None


def test_stop_is_idempotent_without_configuration_or_dependencies(local_project):
    root, _ = local_project
    assert stop(root).returncode == 0
    assert stop(root).returncode == 0
    assert not (root / ".env").exists()
    assert not (root / ".venv").exists()


def test_stop_infra_runs_only_after_application_cleanup(local_project):
    root, processes = local_project
    process = start_launcher(root, processes)
    start = root / "scripts/start-local.sh"
    start.write_text(
        "#!/bin/bash\n"
        "set -eu\n"
        "if lsof -t .local/development/start.lock >/dev/null 2>&1; then exit 92; fi\n"
        "printf '%s\\n' \"$*\" > infra-args\n"
    )
    start.chmod(0o755)
    result = subprocess.run(
        [str(root / "scripts/stop-local.sh"), "--stop-infra"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "infra-args").read_text() == "--stop-infra\n"
    process.wait(timeout=5)


def test_stop_rejects_unknown_arguments(local_project):
    root, _ = local_project
    result = stop(root, "--unknown")
    assert result.returncode == 1
    assert "用法" in result.stderr
