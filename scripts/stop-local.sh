#!/usr/bin/env bash
# 通知持有本项目启动锁的启动器退出，由启动器清理自己创建的应用进程组。
set -euo pipefail

service_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
lock_file="$service_dir/.local/development/start.lock"
stop_infra=false
if [[ $# -eq 1 && "$1" == "--stop-infra" ]]; then
    stop_infra=true
elif [[ $# -ne 0 ]]; then
    printf '%s\n' '用法：./scripts/stop-local.sh [--stop-infra]' >&2
    exit 1
fi

if ! command -v lsof >/dev/null 2>&1; then
    printf '%s\n' '缺少 lsof，无法安全定位本项目的后端启动器，请安装后重试。' >&2
    exit 1
fi

pids=()
identities=()
candidates=""
if [[ -f "$lock_file" ]]; then
    candidates="$(lsof -nP -t -- "$lock_file" 2>/dev/null || true)"
fi
while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    identity="$(ps -p "$pid" -o lstart= -o command= 2>/dev/null || true)"
    case "$identity" in
        *" -m creativity_service.development.launcher" | \
        *" -m creativity_service.development.launcher "*)
            if lsof -nP -a -p "$pid" -d cwd -Fn 2>/dev/null | grep -Fxq "n$service_dir"; then
                pids+=("$pid")
                identities+=("$identity")
            fi
            ;;
    esac
done <<< "$candidates"

if [[ ${#pids[@]} -eq 0 ]]; then
    printf '%s\n' '本项目的后端启动器未运行，无需停止应用。'
else
    for index in "${!pids[@]}"; do
        pid="${pids[$index]}"
        # 不强杀启动器，否则会跳过 API、Worker 和调度器的进程组清理。
        if [[ "$(ps -p "$pid" -o lstart= -o command= 2>/dev/null || true)" == "${identities[$index]}" ]]; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    deadline=$((SECONDS + 35))
    while true; do
        running=()
        for index in "${!pids[@]}"; do
            pid="${pids[$index]}"
            if [[ "$(ps -p "$pid" -o lstart= -o command= 2>/dev/null || true)" == "${identities[$index]}" ]]; then
                running+=("$pid")
            fi
        done
        [[ ${#running[@]} -ne 0 ]] || break
        if (( SECONDS >= deadline )); then
            printf '后端启动器未在期限内停止（进程号：%s），请检查 log/launcher.log。\n' "${running[*]}" >&2
            exit 1
        fi
        sleep 0.2
    done
    # 锁文件保留原 inode；文件存在不代表仍被锁定，删除它可能绕过启动互斥。
    printf '%s\n' '本项目的 API、Worker 和调度器已停止，启动锁已释放。'
fi

if [[ "$stop_infra" == true ]]; then
    "$service_dir/scripts/start-local.sh" --stop-infra
else
    printf '%s\n' '数据库、Redis、MinIO 和已有外部服务继续保留。'
fi
