# 02 本地验证记录

日期：2026-10-01。环境：macOS arm64、Python 3.12.7、Node.js 22.23.3。标准启动和检查入口见 [项目 README](../../README.md)，依赖及配置见 [交接记录](bootstrap.md)。

| 验证项 | 结果 |
| --- | --- |
| `uv sync --locked` | 通过，包含异步数据库所需 greenlet |
| `pnpm install --frozen-lockfile` | 通过，锁文件无需重新解析 |
| Ruff 格式及 lint | 通过 |
| mypy strict | 18 个源文件通过 |
| 后端单元测试 | 11 项通过，包含配置缺失、Redis 分库、错误/请求标识、故障诊断、超时、契约与 SDK 组合 |
| 后端真实集成测试 | 6 项通过，包含依赖就绪、数据库/认证 Redis/存储故障、独立 Worker、迁移版本存储 |
| 前端类型及 ESLint | 通过 |
| 前端 Vitest | 6 项通过，覆盖 401/403/503、字段错误、非 JSON 错误、断网及取消 |
| 前端生产构建 | 通过；当前 antd 公共壳主包约 727 kB、gzip 238 kB，Vite 提示后续可按模块拆包 |
| Playwright 浏览器测试 | 2 项通过，本机使用 Google Chrome；覆盖工作台无运行错误、375 px 窄屏及不存在页面 |
| 实际 HTTP API | `/health/live`、`/health/ready` 返回 200；六个依赖检查均正常 |
| 错误与请求标识 | 两个业务前缀下不存在路径返回 404，JSON 错误 ID 与响应头相同，CORS 暴露 X-Request-ID |
| Vite 开发代理 | `/admin/v1`、`/api/v1`、`/health` 均正确转发；真实浏览器可读取错误及请求标识 |
| OpenAPI | 在线响应与已生成文件一致；无配置的临时目录连续两次离线导出字节一致 |

契约 SHA256：`40b2e0e5b4daa41d152ada2744545cc6faa3823166cfde6156410d6291754daa`。

迁移验证在随机命名的独立测试 schema 中执行升级、重复升级、离线 SQL 和回退，确认版本行含 `channel_id=system`，表/字段中文注释同步，未生成任何索引；测试完成后清理该 schema。实际开发数据库未创建业务表，空修订链不创建版本表。

本机默认 Node.js 25 且没有 uv，因此工具链安装在根目录 `.tools/` 中，验证命令临时增加该目录到 PATH；没有修改全局 Node.js。该目录不纳入源码，CI 显式安装所需运行时和工具。

本次工作区可先在根目录执行 `export PATH="$PWD/.tools/js/node_modules/.bin:$PWD/.tools/uv/bin:$PATH"` 使用已安装工具链，再进入对应工程。新环境按 README 安装正式工具，无需该目录。

Docker 使用本机 Colima。Docker Hub 直连超时后，PostgreSQL/Redis 从公开镜像缓存拉取相同标签并在本机标记为官方标签；现有 MinIO/mc 镜像经版本命令确认与 Compose 固定版本一致。工程 Compose 仍使用公开官方镜像名称。PyPI 直连缓慢，最终依赖源明确记录为清华镜像并生成锁文件。

GitHub Actions 已提供干净环境安装、检查及集成验证配置；当前目录不是 Git 仓库且没有远端，未执行远端流水线或创建提交。真实模型/MCP 协议、业务流程、正式容量和生产部署尚未验证，按后续单元推进。
