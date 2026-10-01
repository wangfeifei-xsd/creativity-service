# 02 工程初始化交接

本文保留 02 的阶段交接，03 后的当前公共设施见 [公共设施交接](core.md)。

对应 [工程初始化方案](../../代码编写执行方案/02-工程初始化.md)。开发规范引用 [rule.md](../../rule.md)，本文记录工程实际配置与验证，不替代业务需求。业务模块由后续方案实现。

## 依赖兼容矩阵

| 组件 | 锁定或验证版本 | 组合验证 |
| --- | --- | --- |
| Python / uv | 3.12（本机 3.12.7）/ 0.10.12 | 锁文件安装、格式、静态类型与 pytest |
| Node.js / pnpm | 22.23.3 / 10.32.1 | 锁文件安装、TypeScript、lint、测试和构建 |
| FastAPI / Pydantic / Settings | 0.142.2 / 2.13.5 / 2.15.0 | 应用工厂、配置错误、生命周期与 JSON Schema |
| SQLAlchemy / psycopg / Alembic | 2.0.54 / 3.3.6 / 1.20.0 | asyncio extra 含 greenlet；真实 PostgreSQL 连接和迁移 |
| Celery / Redis SDK | 5.6.3 / 6.4.0 | 独立 Worker 启动和远程 ping |
| LiteLLM | 1.96.2（03 修订，02 原为 1.81.15） | 03 依赖扫描后升级，验证记录见 core-validation.md；未连接真实供应商 |
| LangGraph | 1.2.12 | 无持久化的小图执行，未启用自动建表 |
| MCP SDK | 1.30.0 | 同一环境导入；未连接真实 MCP 服务 |
| OpenTelemetry SDK / Instrumentation | 1.45.0 / 0.66b0 | API 请求追踪上下文与 Worker 初始化 |
| boto3 | 1.43.106 | MinIO 桶就绪探测和缺失桶诊断 |
| React / antd / React Router | 19.3.0 / 6.6.5 / 7.18.4 | 工作台与窄屏浏览器流程 |
| TypeScript / Vite | 5.9.3 / 7.3.6 | 类型检查和生产构建 |
| openapi-typescript | 7.13.0 | 离线类型生成和内容一致性检查 |
| Vitest / Playwright | 4.1.11 / 1.63.0（03 更新 Vitest） | 请求封装单元测试与浏览器测试工具 |
| PostgreSQL / Redis 服务 | 17（本机 17.11）/ 7.4（本机 7.4.11） | Compose 启动及真实依赖故障测试 |
| MinIO / mc | RELEASE.2025-09-07T16-13-09Z / RELEASE.2025-08-13T08-35-41Z | 显式创建桶、认证与访问验证 |

精确传递依赖及校验和在 `uv.lock`、`pnpm-lock.yaml`。Python 使用显式配置的清华 PyPI 镜像，避免依赖开发者全局源配置；前端使用公开 npm registry。升级运行时或更换依赖源时更新清单、锁文件与本矩阵，并重跑组合验证。供应商协议验证和依赖扫描分别由后续模型单元、03 补齐。

## 目录与登记点

| 路径 | 当前职责与后续入口 |
| --- | --- |
| `src/creativity_service/app.py` | 应用生命周期、路由装配与公共 OpenAPI |
| `src/creativity_service/api/` | 固定版本前缀、探针、基础错误封装与请求标识 |
| `src/creativity_service/core/config.py` | 环境配置加载、必填项与连接格式校验 |
| `src/creativity_service/core/infrastructure.py` | PostgreSQL、Redis、对象存储探测与资源释放 |
| `src/creativity_service/core/observability/` | JSON 日志和 API/Worker 追踪初始化 |
| `src/creativity_service/core/migrations.py` | 系统渠道迁移版本表及迁移锁常量 |
| `src/creativity_service/workers/app.py` | 独立 Celery 装配，无业务任务 |
| `src/creativity_service/modules/`、`integrations/` | 后续业务模块、外部系统适配目录 |
| `alembic/` | 显式迁移环境、修订模板、当前空修订链 |
| `contracts/openapi.json` | 服务端路由唯一生成的接口契约 |
| `tests/`、`tests/integration/` | 隔离单元测试、真实基础设施和迁移验证 |
| `../creativity-web/src/app/` | antd 主题、基础布局和应用入口 |
| `../creativity-web/src/api/` | 请求封装、Token 存取、表单错误、生成类型 |
| `../creativity-web/src/components/` | 页面容器及加载、空数据、错误状态 |
| `../creativity-web/src/features/registry.ts` | 页面组件登记；03/06 对接服务端导航键 |

前后端错误基础形状为 `error: {code, message, fields: [{path, message}]}`，错误正文及响应头共同返回请求标识。就绪探针的 503 单独返回 `status/checks`，用于诊断基础设施，不套用业务错误形状。公开探针没有业务数据和认证上下文；API 目前只有探针，业务路由器为空。

## 配置清单

下列名称统一带 `CREATIVITY_` 前缀，`.env.example` 给出仅用于本地的完整可运行值。生产连接串、对象存储凭据与部署地址只通过环境注入。默认配置文件相对于启动时的工作目录解析。

| 配置项（省略统一前缀） | 必填 / 默认 | 用途 |
| --- | --- | --- |
| `ENVIRONMENT` | 默认 development | development、test 或 production |
| `SERVICE_NAME` | 默认 creativity-service | 追踪服务名前缀 |
| `LOG_LEVEL` | 默认 INFO | DEBUG、INFO、WARNING、ERROR |
| `DATABASE_URL` | 必填 | postgresql+psycopg 连接地址 |
| `REDIS_CACHE_URL` | 必填 | 缓存 Redis，本地数据库 0 |
| `REDIS_AUTH_URL` | 必填 | 认证 Redis，本地数据库 1 |
| `CELERY_BROKER_URL` | 必填 | 队列 Redis，本地数据库 2 |
| `CELERY_RESULT_URL` | 必填 | 结果 Redis，本地数据库 3 |
| `REDIS_KEY_PREFIX` | 默认 creativity | 本部署 Redis 前缀 |
| `S3_ENDPOINT_URL` | 必填 | S3 兼容服务的 HTTP/HTTPS 地址 |
| `S3_REGION` | 必填 | 签名区域 |
| `S3_ACCESS_KEY_ID`、`S3_SECRET_ACCESS_KEY` | 必填 | 仅服务端读取的存储凭据 |
| `S3_BUCKET` | 必填 | 必须提前创建的桶 |
| `HEALTH_TIMEOUT_SECONDS` | 默认 3 秒 | 各就绪探针的超时上限 |
| `CORS_ORIGINS` | 默认空数组 | 允许跨域读取 API 的来源列表；本地前端优先同源代理 |
| `OTEL_ENABLED` | 默认 false | 是否导出追踪；本地追踪上下文仍可用于日志关联 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 启用导出时必填 | OTLP HTTP 基地址，程序追加 /v1/traces |

`DEV_POSTGRES_USER/PASSWORD/DB/PORT`、`DEV_REDIS_PORT`、`DEV_S3_PORT/CONSOLE_PORT` 仅由开发 Compose 消费。前端只有 `API_PROXY_TARGET`，仅开发代理读取，不暴露给浏览器。修改本地端口时同步修改对应连接配置。

## Redis 与存储交接

| 用途 | 本地 Redis 数据库 | 键约定与实现状态 |
| --- | --- | --- |
| 缓存 | 0 | `creativity:cache:{channel_id}:...`，03 实现受控键构造 |
| 认证 | 1 | `creativity:auth:token:{token_digest}`，04 保存含渠道的上下文并设置 TTL |
| Celery broker | 2 | 已配置 `creativity:celery:broker:` 全局前缀 |
| Celery result | 3 | 已配置 `creativity:celery:result:` 前缀；当前业务任务结果存储关闭 |

启动校验拒绝四种用途使用相同 Redis 主机、端口及数据库组合；生产可以改为不同实例，仍保留用途前缀。当前没有业务缓存、认证会话、文件或任务记录。03/11 接入渠道键、受信任务上下文与持久化运行核对；Celery 自身队列元数据不作为业务任务的状态依据。

本地桶为 `creativity-dev`，由 `make infra-up` 显式创建。API 只检查桶，启动不自动创建桶或业务表。文件路径和元数据授权由 03 实现。

迁移元数据表 `creativity_alembic_version` 只有 `version_num`、`channel_id` 两个普通字符串列，表与列均有中文注释。迁移写入显式绑定系统渠道 `system`，没有物理主键、唯一索引或列默认值。迁移入口用 PostgreSQL 事务锁 `71977002001` 串行化；空修订链不创建元数据表。03 将该表纳入控制面模型归档，并登记公共业务元数据。

## 验证命令与后续边界

完整入口见两工程 README。基础检查使用 `make check` 与 `pnpm check`；真实基础设施使用 `make integration`；浏览器使用 `pnpm test:e2e`。契约导出与校验不需要 `.env`、数据库或生产凭据。

CI 配置为根目录 `.github/workflows/ci.yml`：干净环境按锁文件安装、检查契约、启动独立 Compose 项目、执行集成测试、安装浏览器后执行页面测试。当前工作区尚无 Git 仓库或远端，本次记录本地执行结果，未声称远端 CI 已运行。

02 不实现登录、权限、渠道、模型调用、任务业务或数据库业务模型。03 接续数据模型归档、公共协议和 DDL/依赖检查；04/05 接续真实认证与渠道；06 接续导航和工作区；11 接续任务渠道校验与可靠执行。
