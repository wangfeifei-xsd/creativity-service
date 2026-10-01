# creativity-service

Python 3.12 + FastAPI，API 与 Celery Worker 分进程运行。使用 uv 0.10.12；完整启动顺序见 [项目 README](../README.md)，开发规范见 [rule.md](../rule.md)。

## 命令

| 命令 | 用途 |
| --- | --- |
| `uv sync --locked` | 按锁文件创建虚拟环境并安装依赖 |
| `make infra-up` | 启动 PostgreSQL、Redis、MinIO，显式创建开发桶 |
| `make dev` | 启动可热重载的 API，监听 127.0.0.1:8000 |
| `uv run creativity-api` | 启动不热重载的 API |
| `make worker` | 独立启动单进程开发 Worker |
| `make format` | 格式化并修复可自动修复的 lint 问题 |
| `make check` | 格式、lint、严格类型、单元测试与契约一致性检查 |
| `make integration` | 真实基础设施与 Worker 冒烟验证 |
| `uv run creativity-openapi` | 不连接基础设施，导出 contracts/openapi.json |
| `uv run creativity-openapi --check` | 校验当前契约内容 |
| `make migrate` | 显式升级至 0003_channels（公共、账号授权及渠道表） |
| `make contracts` | 生成公共及渠道 JSON Schema、样例与 OpenAPI |
| `make channels-init` | 幂等初始化系统渠道，不创建业务凭据 |
| `make model-check` | 校验全量模型档案、迁移源码及公共定义 |
| `make storage-audit` | 核验实际 PostgreSQL schema 与框架表 |
| `make dependency-audit` | 扫描 Python 依赖公告 |
| `make infra-down` | 停止开发容器，保留命名卷 |

## 配置

从工程当前目录读取 `.env`，进程环境变量优先。可直接通过环境注入配置而不提供 `.env`。启动前校验全部必要连接项，缺失或格式错误立即失败；外部服务断线不会伪装成配置错误，而是反映在就绪探针中。日志输出 JSON；响应头 `X-Request-ID` 与错误正文中的 `request_id` 一致。

配置项清单、Redis 分库及前缀约定见 [bootstrap.md](docs/bootstrap.md)。示例凭据仅用于本地容器，生产值从部署环境注入。开启 OpenTelemetry 导出需同时配置 `CREATIVITY_OTEL_ENABLED=true` 和 `CREATIVITY_OTEL_EXPORTER_OTLP_ENDPOINT`，端点为采集器基地址（例如 `http://127.0.0.1:4318`）。API 和 Worker 使用独立的追踪服务名，进程关闭时刷新并关闭导出器。

## 扩展入口

`api/routes.py` 固定 `/admin/v1` 与 `/api/v1` 前缀。业务模块放在 `modules/<模块>/`，外部适配放在 `integrations/`，任务装配放在 `workers/`。当前 Worker 仅验证进程与队列连接，不提供业务任务或持久化结果；03 已提供受信上下文协议，11 接入持久化运行核对后登记业务任务。

迁移链当前包含 `0001_core` 的 10 张公共表、`0002_iam` 的 5 张 IAM 表及 `0003_channels` 的 9 张渠道表。控制面版本表为 `creativity_alembic_version`，显式写入系统渠道 `system`，具备中文注释且不创建唯一索引；迁移命令通过事务锁串行执行。03 已完成数据模型、公共契约及设施，详见 [公共设施交接](docs/core.md) 和 [数据模型索引](docs/data-model/README.md)。

账号初始化命令为 `uv run creativity-iam init-admin --login-name admin --display-name 管理员`，通过终端隐藏输入密码。撤销补偿使用 `make iam-reconcile`。账号、登录、成员、资源授权与审计已经接通；05 已注入真实渠道、工作区及服务 Key 状态，系统渠道须先运行 `make channels-init`。完整接口及交接见 [IAM 说明](docs/iam.md)。

渠道接口、服务凭据交换、生命周期事件和后续模块端口见 [05 交接](docs/channels.md)。
