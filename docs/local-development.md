# 本地启停与日志

在 `creativity-service` 目录执行：

```bash
./scripts/start-local.sh
```

需要 Python 3.12、uv 0.10.12；只有缺少可用依赖时才需要已经启动的 Docker 和 Compose v2。脚本会查找 PATH 中的 uv，也支持项目上级 `.tools/uv/bin/uv`。首次运行从 `.env.example` 创建 `.env`，按锁文件准备 Python 依赖，然后检查基础设施、执行 Alembic 迁移、初始化系统渠道，启动 API、单进程 Worker 和定时调度器。

Compose 优先使用 `docker compose` 插件，也支持 PATH 中的独立 `docker-compose` 或项目上级 `.tools/docker/docker-compose`。

如果提示无法连接 `~/.colima/default/docker.sock` 或该文件不存在，通常是 Colima 尚未启动。先执行 `colima start`，等 `docker info` 成功后重新运行启动脚本。使用 Docker Desktop 时先打开应用并等待引擎就绪。若仍无法连接，用 `docker context ls` 核对当前连接目标，同时检查 `DOCKER_HOST`、`DOCKER_CONTEXT` 是否覆盖了目标配置。

API 默认地址为 `http://127.0.0.1:8000`，启用源码热重载。管理员仍通过 `uv run creativity-iam init-admin --login-name admin --display-name 管理员` 交互设置密码；前端按其自身 README 启动。

## 依赖复用

先使用 `.env`、`.env.local` 或进程环境中配置的地址和凭据验证真实连接。地址可用就直接复用，不执行 Docker 启动或重建。

配置的本机地址没有服务时，再检查本机常用端口 MySQL `3306`、Milvus `19530`、Redis `6379`、S3/MinIO `9000`，兼容 IPv4 和 IPv6。现有实例只有在当前数据库、凭据及各 Redis 数据库编号都通过校验时才会复用。解析出的地址传给本次启动的子进程，`.env` 保持原配置。已有端口被占用但认证或协议校验失败时退出，提示修正配置；远端地址不可达时也不会用新建本地服务替代。

没有可复用服务才按需启动对应 Compose 服务，沿用项目已有容器和命名卷，不使用 `down -v`。MySQL 必须为 8.0，业务数据使用独立的 `creativity` 数据库；复用租号实例时只配置同一地址和凭据，不使用租号数据库。现有实例须先按 [初始化说明](../sql/README.md) 创建目标空库。Milvus 使用独立服务及持久卷，脚本通过 REST v2 验证连接；不再安装 PostgreSQL 扩展。

服务还依赖对象存储，因此会同时检查并按需启动 MinIO；本地配置的桶不存在时创建。Redis 的缓存、认证、队列与结果仍使用独立数据库。新建 Docker Redis 支持 URL 中的密码；自定义 TLS/ACL 使用自行配置的现有实例。

## 命令与退出

| 命令 | 行为 |
| --- | --- |
| `./scripts/start-local.sh` / `make local` | 准备依赖与数据，启动 API、Worker、调度器 |
| `./scripts/start-local.sh --check` / `make local-check` | 只检查连接与向量能力，不启动容器、建桶、创建集合或迁移 |
| `./scripts/start-local.sh --infra-only` / `make infra-up` | 只检查并准备基础设施 |
| `./scripts/start-local.sh --prepare-only` / `make local-prepare` | 准备基础设施、迁移与系统渠道后退出 |
| `./scripts/start-local.sh --port 8001 --no-reload` | 指定 API 端口并关闭热重载 |
| `./scripts/stop-local.sh` / `make local-stop` | 停止一键启动的 API、Worker、调度器并释放启动锁，保留依赖 |
| `./scripts/stop-local.sh --stop-infra` | 先停止应用，再停止项目 Docker 依赖，保留数据卷 |
| `./scripts/start-local.sh --stop-infra` / `make infra-down` | 停止项目 Docker 依赖，保留数据卷 |

脚本以前台方式运行，等待 API 就绪后持续检查子进程。重复运行由 `.local/development/start.lock` 拦截；API 端口已被其他进程占用时退出。按 `Ctrl+C`、发送 `SIGTERM` 或在另一个终端执行 `./scripts/stop-local.sh`，都会通过启动器停止本次启动的应用进程；子进程异常退出也会清理本次启动的其余进程。数据库、Redis、MinIO 和已有外部服务继续保留。需要同时停止项目 Docker 依赖时执行 `./scripts/stop-local.sh --stop-infra`，数据卷保留。

默认停止应用只需要系统 `lsof` 和 `ps`，不读取 `.env` 或安装 Python 依赖；通过启动锁持有者、项目目录和启动命令定位进程，因此也兼容已经运行的旧启动脚本。`--stop-infra` 额外调用现有启动入口，需要 uv、本地配置和 Compose。未运行时可重复执行。锁文件本身会保留，不应手动删除；启动器退出后文件锁会自动释放。独立使用 `make dev`、`make worker`、`make scheduler` 启动的进程仍在各自终端按 `Ctrl+C` 停止，不在此脚本的清理范围。

## 日志

应用日志默认输出到 `creativity-service/log/`，同时保留控制台输出。直接使用 `make dev`、`make worker`、`make scheduler` 也会写文件。

| 文件 | 内容 |
| --- | --- |
| `api.log` | API 结构化日志、请求状态、耗时和请求标识 |
| `worker.log` | Worker 结构化日志 |
| `scheduler.log` | 定时调度器结构化日志 |
| `launcher.log` | 依赖复用、启动和退出记录 |
| `api-console.log`、`worker-console.log`、`scheduler-console.log` | 启动脚本捕获的子进程控制台输出，包括应用初始化前的错误 |

使用预派生 Worker 时，子进程分别写 `worker-<进程号>.log`，避免多进程争用日志轮转。结构化日志沿用现有请求、渠道上下文和诊断字段白名单，不记录请求正文或认证字段。

`CREATIVITY_LOG_DIRECTORY` 默认 `log`，相对路径按服务工作目录解析；`CREATIVITY_LOG_MAX_BYTES` 默认 `10485760`（10 MiB），`CREATIVITY_LOG_BACKUP_COUNT` 默认 `5`。文件达到上限后保留 `.1` 至 `.5` 的历史文件。日志及本地进程状态已被 Git 忽略。

```bash
tail -f log/api.log log/worker.log log/scheduler.log
```
