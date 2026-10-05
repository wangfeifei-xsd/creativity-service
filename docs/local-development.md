# 本地启动与日志

在 `creativity-service` 目录执行：

```bash
./scripts/start-local.sh
```

需要 Python 3.12、uv 0.10.12；只有缺少可用依赖时才需要已经启动的 Docker 和 Compose v2。脚本会查找 PATH 中的 uv，也支持项目上级 `.tools/uv/bin/uv`。首次运行从 `.env.example` 创建 `.env`，按锁文件准备 Python 依赖，然后检查基础设施、执行 Alembic 迁移、初始化系统渠道，启动 API、单进程 Worker 和定时调度器。

Compose 优先使用 `docker compose` 插件，也支持 PATH 中的独立 `docker-compose` 或项目上级 `.tools/docker/docker-compose`。

API 默认地址为 `http://127.0.0.1:8000`，启用源码热重载。管理员仍通过 `uv run creativity-iam init-admin --login-name admin --display-name 管理员` 交互设置密码；前端按其自身 README 启动。

## 依赖复用

先使用 `.env` 或进程环境中配置的地址和凭据验证真实连接。地址可用就直接复用，不执行 Docker 启动或重建。

配置的本机地址没有服务时，再检查本机常用端口 PostgreSQL `5432`、Redis `6379`、S3/MinIO `9000`，兼容 IPv4 和 IPv6。现有实例只有在当前数据库、凭据及各 Redis 数据库编号都通过校验时才会复用。解析出的地址传给本次启动的子进程，`.env` 保持原配置。已有端口被占用但认证或协议校验失败时退出，提示修正配置；远端地址不可达时也不会用新建本地服务替代。

没有可复用服务才按需启动对应 Compose 服务，沿用项目已有容器和命名卷，不使用 `down -v`。PostgreSQL 使用现有的 pgvector 镜像构建定义。向量存储就是 PostgreSQL 中的 `vector` 扩展，脚本会验证真实向量距离查询，扩展已启用时直接复用；扩展文件存在但未启用时显式启用。若现有项目 PostgreSQL 17 容器缺少扩展文件，会在核对数据库集群标识和持久卷后，先构建向量镜像，再替换同一容器并保留数据卷。其他来源的 PostgreSQL 缺少扩展文件时提示为原实例安装，不另建数据库。

服务还依赖对象存储，因此会同时检查并按需启动 MinIO；本地配置的桶不存在时创建。Redis 的缓存、认证、队列与结果仍使用独立数据库。新建 Docker Redis 支持 URL 中的密码；自定义 TLS/ACL 使用自行配置的现有实例。

## 命令与退出

| 命令 | 行为 |
| --- | --- |
| `./scripts/start-local.sh` / `make local` | 准备依赖与数据，启动 API、Worker、调度器 |
| `./scripts/start-local.sh --check` / `make local-check` | 只检查连接与向量能力，不启动容器、建桶、启用扩展或迁移 |
| `./scripts/start-local.sh --infra-only` / `make infra-up` | 只检查并准备基础设施 |
| `./scripts/start-local.sh --prepare-only` / `make local-prepare` | 准备基础设施、迁移与系统渠道后退出 |
| `./scripts/start-local.sh --port 8001 --no-reload` | 指定 API 端口并关闭热重载 |
| `./scripts/start-local.sh --stop-infra` / `make infra-down` | 停止项目 Docker 依赖，保留数据卷 |

脚本以前台方式运行，等待 API 就绪后持续检查子进程。重复运行由 `.local/development/start.lock` 拦截；API 端口已被其他进程占用时退出。按 `Ctrl+C` 或发送 `SIGTERM` 会停止本次启动的应用进程；子进程异常退出也会清理本次启动的其余进程。数据库、Redis、MinIO 和已有外部服务继续保留。需要停止项目 Docker 依赖时使用 `make infra-down`，数据卷保留。

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
