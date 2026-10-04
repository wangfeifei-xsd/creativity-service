# Creativity Service

Creativity AI 能力平台的后端服务，提供模型接入、Agent 编排、MCP 工具调用、会话与记忆、用量预算和效果评测能力。业务系统通过统一 API 调用 Agent，通过 MCP 提供业务工具。

服务采用 Python + FastAPI，API、Celery Worker 与定时调度器分别运行。平台配置和运行管理由 [Creativity Web](../creativity-web/README.md) 提供。

## 目录

- [主要功能](#主要功能)
- [技术栈](#技术栈)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置](#配置)
- [开发与测试](#开发与测试)
- [项目结构](#项目结构)
- [文档](#文档)
- [参与开发](#参与开发)

## 主要功能

- **渠道与权限**：渠道隔离、账号认证、成员管理、资源授权及操作审计。
- **模型与 Agent**：模型配置、提示词与 Skills 管理、Agent 版本发布及运行编排。
- **工具与接入**：MCP 连接、工具发现与执行、业务身份委托及统一调用 API。
- **任务与内容**：同步、异步和 SSE 结果交付，会话、结构化记忆、文件产物及数据清理。
- **用量与评测**：调用计量、预算控制、样本管理、批量评测及发布门禁。

## 技术栈

| 用途 | 技术 |
| --- | --- |
| API 与数据校验 | FastAPI、Pydantic |
| 数据库与迁移 | PostgreSQL 17、SQLAlchemy、Alembic |
| 缓存与任务 | Redis 7.4、Celery |
| 对象存储 | S3 兼容接口，本地使用 MinIO |
| 模型与编排 | LiteLLM、LangGraph、MCP SDK |
| 可观测性 | JSON 日志、OpenTelemetry |
| 工程检查 | Ruff、mypy、pytest |

依赖版本由 [pyproject.toml](pyproject.toml) 和 [uv.lock](uv.lock) 管理。

## 环境要求

| 工具 | 要求 |
| --- | --- |
| Python | 3.12 |
| uv | 0.10.12 |
| Docker | 支持 Docker Compose v2 |
| Make | 用于执行项目开发命令 |

以下命令均在 `creativity-service` 项目目录执行。开发依赖由 Docker Compose 启动，API、Worker 和调度器在本地 Python 环境运行。

## 快速开始

### 1. 安装依赖

首次使用时复制配置模板，再安装锁定版本的依赖：

```bash
cp .env.example .env
uv sync --locked
```

### 2. 初始化本地环境

```bash
make infra-up
make migrate
make channels-init
uv run creativity-iam init-admin --login-name admin --display-name 管理员
```

上述命令依次启动 PostgreSQL、Redis 和 MinIO，创建开发存储桶，执行数据库迁移，初始化系统渠道和平台管理员。管理员初始化仅在首次安装时执行；密码通过终端交互输入，首次登录后需要修改初始密码。

空库也可直接执行完整归档 [sql/init.sql](sql/init.sql)，一次创建当前全部表、索引、中文注释、系统渠道和迁移版本记录。执行方式与后续管理员初始化见 [SQL 初始化说明](sql/README.md)。

### 3. 启动服务

在一个终端启动 API：

```bash
make dev
```

在另一个终端进入同一项目目录，启动 Worker：

```bash
make worker
```

再打开一个终端进入同一项目目录，启动定时调度器：

```bash
make scheduler
```

Worker 执行后台任务，调度器触发运行补偿、用量导出、连接检查和数据清理。需要管理页面时，继续按 [前端快速开始](../creativity-web/README.md#快速开始) 启动前端，并使用刚创建的管理员登录。

### 4. 检查服务

| 入口 | 地址 |
| --- | --- |
| API 文档 | [Swagger UI](http://127.0.0.1:8000/docs) |
| 存活检查 | [GET /health/live](http://127.0.0.1:8000/health/live) |
| 就绪检查 | [GET /health/ready](http://127.0.0.1:8000/health/ready) |

```bash
curl -fsS http://127.0.0.1:8000/health/ready
```

就绪检查会报告数据库、Redis 和对象存储状态，必要依赖不可用时返回 HTTP 503。管理接口使用 `/admin/v1` 前缀，业务调用接口使用 `/api/v1` 前缀；认证及调用示例见 [统一 API 接入指南](docs/unified-api.md)。

结束开发时，在各终端按 `Ctrl+C` 停止进程，再执行 `make infra-down` 停止开发依赖；数据库和对象存储的命名卷会保留。

## 配置

服务从当前目录读取 `.env`，进程环境变量优先。基础配置模板见 [.env.example](.env.example)，详细说明见 [工程配置文档](docs/bootstrap.md)。

| 配置项 | 用途 |
| --- | --- |
| `CREATIVITY_DATABASE_URL` | PostgreSQL 连接地址 |
| `CREATIVITY_REDIS_CACHE_URL`、`CREATIVITY_REDIS_AUTH_URL` | 缓存与认证 Redis 连接 |
| `CREATIVITY_CELERY_BROKER_URL`、`CREATIVITY_CELERY_RESULT_URL` | 任务队列与结果 Redis 连接 |
| `CREATIVITY_S3_*` | 对象存储端点、区域、凭据与存储桶 |
| `CREATIVITY_CORS_ORIGINS` | 允许的浏览器来源，使用 JSON 数组 |
| `CREATIVITY_DELETION_LEDGER_PATH` | 删除意图清单的持久化目录 |
| `CREATIVITY_OTEL_ENABLED`、`CREATIVITY_OTEL_EXPORTER_OTLP_ENDPOINT` | 追踪导出开关及 OTLP HTTP 端点 |

本地默认端口为 PostgreSQL `55432`、Redis `56379`、MinIO API `59000`、MinIO 控制台 `59001`。缓存、认证、任务队列和任务结果分别使用独立的 Redis 数据库。

示例凭据用于本地开发，部署时通过环境注入实际凭据。删除清单的共享持久卷要求见 [数据生命周期文档](docs/data-lifecycle.md)；向量检索、MCP OAuth、脚本执行和外部身份等可选能力见 [扩展配置](docs/enhancements/configuration.md)。

## 开发与测试

| 命令 | 说明 |
| --- | --- |
| `make dev` | 启动支持热重载的 API |
| `make worker` | 启动本地单进程 Worker |
| `make scheduler` | 启动 Celery Beat 调度器 |
| `make format` | 格式化代码并修复可自动修复的 lint 问题 |
| `make check` | 执行格式、lint、严格类型、非集成测试、契约及存储定义检查 |
| `make test` | 运行非集成测试 |
| `make integration` | 运行依赖真实基础设施的集成测试 |
| `make migrate` | 将数据库迁移至当前版本 |
| `make sql` / `make sql-check` | 生成 / 校验完整初始化 SQL 归档 |
| `make openapi` | 导出完整接口和业务接口的 OpenAPI |
| `make contracts` | 生成各模块 JSON Schema、样例及 OpenAPI |
| `make model-check` | 校验模型档案、迁移源码及存储定义 |
| `make storage-audit` | 检查实际数据库结构 |
| `make dependency-audit` | 扫描 Python 依赖公告 |
| `uv build --wheel` | 构建 Python 分发包 |

`make check` 无需启动外部服务。执行 `make integration` 或 `make storage-audit` 前，需要准备 `.env` 并启动开发依赖；数据库结构检查还需先运行迁移。集成测试使用隔离测试环境，相关用例会启动独立 Worker。

接口变更后执行 `make openapi`，再按 [前端接口类型生成说明](../creativity-web/README.md#接口类型生成) 更新前端类型。完整命令以 [Makefile](Makefile) 为准，真实模型和组合场景的验证范围见 [验收文档](docs/acceptance/README.md)。

## 项目结构

```text
creativity-service/
├── src/creativity_service/
│   ├── api/             # 路由、健康检查与 OpenAPI
│   ├── core/            # 配置、认证、存储、契约及公共设施
│   ├── modules/         # 平台功能模块
│   ├── integrations/    # 模型、工具及外部系统集成
│   ├── workers/         # Celery 装配、执行、调度及补偿
│   ├── app.py           # FastAPI 应用工厂
│   └── cli.py           # API 启动入口
├── alembic/             # 数据库迁移
├── contracts/           # OpenAPI、JSON Schema 与示例
├── deploy/              # 开发依赖及可选能力的容器配置
├── docs/                # 模块说明、数据模型与验证记录
├── examples/            # 接入和配置示例
├── scripts/             # 文档、契约及验收工具
├── sql/                 # 完整空库初始化 SQL 与执行说明
├── sdks/                # 调用 SDK
├── tests/               # 单元、契约、集成及端到端测试
├── .env.example         # 本地配置模板
├── Makefile             # 常用开发命令
└── pyproject.toml       # Python 依赖与工具配置
```

## 文档

| 主题 | 入口 |
| --- | --- |
| API 与客户端 | [接入指南](docs/unified-api.md) · [调用示例](examples/backend/README.md) · [SDK](sdks/README.md) |
| 账号与渠道 | [认证与授权](docs/iam.md) · [渠道管理](docs/channels.md) |
| 模型与配置 | [模型](docs/models.md) · [提示词](docs/prompts.md) · [Skills](docs/skills.md) · [Agent](docs/agents.md) |
| 工具与业务接入 | [MCP](docs/mcp.md) · [工具](docs/tools.md) · [业务接入](docs/integrations.md) · [配置示例](docs/configuration-delivery.md) |
| 运行与会话 | [任务运行](docs/runs.md) · [运行编排](docs/runtime.md) · [SSE 协议](docs/runtime-sse.md) · [会话](docs/conversations.md) |
| 记忆与数据 | [记忆](docs/memory.md) · [数据生命周期](docs/data-lifecycle.md) · [数据模型](docs/data-model/README.md) |
| 用量与质量 | [用量和预算](docs/usage.md) · [效果评测](docs/evaluations.md) |
| 扩展能力 | [配置说明](docs/enhancements/configuration.md) · [向量环境](deploy/vector.md) |
| 验证与验收 | [组合验收](docs/acceptance/README.md) · [业务接入验证](docs/business-independence-validation.md) · [扩展验证](docs/enhancements/verification.md) |

## 参与开发

开发前阅读 [项目规则](../rule.md)、[技术方案](../技术方案.md) 和对应的 [模块需求](../需求文档/00-需求总纲.md)。公共设施与模块接入方式见 [开发文档](docs/core.md)。提交变更前执行 `make check`，涉及基础设施或跨模块行为时补充相应集成验证。
