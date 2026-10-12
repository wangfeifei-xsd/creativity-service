#!/usr/bin/env bash
# 统一从服务目录加载 dev 配置；不使用 source 执行配置文件中的内容。
set -euo pipefail

service_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$service_dir"

if command -v uv >/dev/null 2>&1; then
    uv_command="$(command -v uv)"
elif [[ -x ../.tools/uv/bin/uv ]]; then
    uv_command=../.tools/uv/bin/uv
else
    printf '%s\n' '缺少 uv，请安装项目要求的 uv 0.10.12 后重试。' >&2
    exit 1
fi

exec "$uv_command" run --locked python -m creativity_service.development.launcher "$@"
