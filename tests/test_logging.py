"""验证真实文件日志、重复配置和轮转后的中文诊断内容。"""

import json
import logging

import pytest

from creativity_service.core.observability import configure_logging, request_id_context


@pytest.fixture
def logging_root():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    root.handlers = []
    try:
        yield root
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        root.handlers = handlers
        root.setLevel(level)


def test_file_logging_keeps_request_id_and_does_not_duplicate_after_reconfigure(
    settings, tmp_path, logging_root
):
    settings = settings.model_copy(update={"log_directory": tmp_path})
    configure_logging(settings, "api")
    configure_logging(settings, "api")
    token = request_id_context.set("request-file-test")
    try:
        logging.getLogger("uvicorn.error").info(
            "服务已启动", extra={"authorization": "不应落盘的秘密"}
        )
    finally:
        request_id_context.reset(token)
    records = [json.loads(line) for line in (tmp_path / "api.log").read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["event"] == "服务已启动"
    assert records[0]["request_id"] == "request-file-test"
    assert "秘密" not in (tmp_path / "api.log").read_text()


def test_file_logging_rotates_and_bounds_backups(settings, tmp_path, logging_root):
    settings = settings.model_copy(
        update={"log_directory": tmp_path, "log_max_bytes": 1024, "log_backup_count": 2}
    )
    configure_logging(settings, "worker")
    logger = logging.getLogger("celery")
    for number in range(40):
        logger.info("任务诊断 %s %s", number, "中文内容" * 15)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "worker.log",
        "worker.log.1",
        "worker.log.2",
    ]
    lines = (tmp_path / "worker.log").read_text().splitlines()
    assert "任务诊断 39" in json.loads(lines[-1])["event"]
