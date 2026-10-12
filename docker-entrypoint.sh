#!/usr/bin/env bash
# 测试环境单容器启动：API + Celery Worker + 调度器共用该容器。
# 传入参数时直接执行参数命令（例如：alembic upgrade head、python -c ...）。
set -euo pipefail

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

mkdir -p "${CREATIVITY_LOG_DIRECTORY:-/app/log}" \
         "${CREATIVITY_DELETION_LEDGER_PATH:-/app/.local/deletion-ledger}"

celery -A creativity_service.workers.app:app worker \
  --pool=solo --loglevel=INFO --hostname="creativity@%h" &
celery -A creativity_service.workers.app:app beat \
  --loglevel=INFO &

exec uvicorn creativity_service.app:create_app --factory \
  --host 0.0.0.0 --port 8000 --no-access-log
