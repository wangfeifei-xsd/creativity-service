# 工程脚本

本地启动使用 `./scripts/start-local.sh` 或 `make local`，停止应用使用 `./scripts/stop-local.sh` 或 `make local-stop`。以下命令均在 `creativity-service` 目录执行；完整启停参数见 [本地开发](../docs/local-development.md)。

## 日常开发

| 文件 | 用途 | 入口与引用 |
| --- | --- | --- |
| [start-local.sh](start-local.sh) | 定位服务目录，准备配置与 Python 环境，调用本地启动器 | `make local`、`make local-check`、`make local-prepare`、`make infra-up`、`make infra-down`；启动集成测试 |
| [stop-local.sh](stop-local.sh) | 通知本项目启动器清理应用并释放启动锁；可选停止 Docker 依赖，保留数据卷 | `make local-stop`；`./scripts/stop-local.sh --stop-infra` |
| [render_data_model.py](render_data_model.py) | 从模型清单生成字段文档，或检查文档是否过期 | `uv run python scripts/render_data_model.py`；检查已纳入 `make check` 和 `make model-check` |
| [render_init_sql.py](render_init_sql.py) | 从当前模型、迁移基线与冻结数据源分别生成表结构和初始数据 SQL，或同时检查两份归档 | `make sql`、`make sql-check`；检查已纳入 `make check` 和 `make model-check` |

## 组合验收

这四个文件组成同一套验收工具，其中两个是供入口导入的辅助模块。运行方法和环境要求见 [测试指南](../docs/testing.md)。

| 文件 | 用途 | 入口与引用 |
| --- | --- | --- |
| [verify_acceptance.py](verify_acceptance.py) | 按阶段执行检查、集成、浏览器、故障和性能验收并汇总证据 | `uv run python -m scripts.verify_acceptance --stage all` |
| [render_acceptance.py](render_acceptance.py) | 将需求、测试收集记录和 JUnit 结果生成需求追踪表 | 由验收入口调用；也可用 `uv run python -m scripts.render_acceptance --output <本次验收目录>` 重新生成追踪表；契约测试引用 |
| [acceptance_matrix.py](acceptance_matrix.py) | 保存需求与测试的映射、负责方案及验收范围 | 由 `render_acceptance.py` 导入，无独立执行入口 |
| [acceptance_report.py](acceptance_report.py) | 汇总数据库、故障、JUnit 证据并判断发布关口 | 由 `verify_acceptance.py` 导入；契约测试引用，无独立执行入口 |

## 专项盘点与历史复现

| 文件 | 用途 | 入口与引用 |
| --- | --- | --- |
| [inventory_access.py](inventory_access.py) | 为方案 19 盘点渠道、旧 HTTP 连接和历史引用，输出标识与摘要 | `uv run python scripts/inventory_access.py --output <输出文件>`；兼容迁移集成测试仍调用 `inventory()` |
| [verify_business_independence.py](verify_business_independence.py) | 为方案 23 创建固定版本的独立检出，构建并验证三渠道业务接入 | 由 [接入验证样例](../examples/onboarding/README.md) 引用；使用 `--service-ref`、`--web-ref` 指定待测版本 |

`verify_business_independence.py` 默认检出服务端 `a28b59f` 和前端 `305a077`，用于历史版本复现。当前工作区的组合验收使用 `verify_acceptance.py`。专项脚本有明确的复现或测试引用，按需使用；本地启动不依赖它们。
