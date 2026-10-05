# 检查与验收

以下命令在服务端目录执行；完整定义见 [Makefile](../Makefile)。

| 命令 | 用途 |
| --- | --- |
| `make check` | 格式、lint、类型、非集成测试、契约、模型和初始化 SQL 一致性 |
| `make test` | 非集成测试 |
| `make integration` | 真实 PostgreSQL、Redis、对象存储及 Worker 集成验证 |
| `make model-check` | 数据模型、初始化 SQL 与存储定义检查 |
| `make storage-audit` | 当前数据库结构审查 |
| `make dependency-audit` | Python 依赖公告扫描 |
| `uv build --wheel` | 分发包构建 |

`make check` 无需启动外部服务。集成验证先按 [本地开发](local-development.md) 准备基础设施；前端检查与浏览器配置见 [前端 README](../../creativity-web/README.md#开发与测试)。

## 组合验收

入口为 [verify_acceptance.py](../scripts/verify_acceptance.py)，使用当前 `.env` 指定的开发环境。测试建立隔离 schema、Redis 前缀和测试对象；`checks` 阶段会迁移开发库。

```bash
uv run python -m scripts.verify_acceptance --stage all
```

默认输出 `.local/acceptance/<UTC时间>/`，包含命令、退出码、JUnit、环境、源码摘要、需求追踪和发布关口。本地输出已被 Git 忽略，用完可删除。分阶段复现时指定相同输出目录：

```bash
uv run python -m scripts.verify_acceptance --stage integration --output .local/acceptance/my-run
uv run python -m scripts.verify_acceptance --stage report --output .local/acceptance/my-run
```

支持 `checks/integration/browser/onboarding/faults/performance/report`。退出码 1 表示执行失败，2 表示汇总后仍有阻断；缺少记录或跳过不能视为通过。性能采样须记录资源、数据量、并发和真实外部调用边界。

真实供应商兼容、实际用量及同候选评测需要配置真实服务。当前输出目录的 `providers.json` 保存 `combinations`，每项记录 `provider`、`protocol`、`model`、`status` 和 `run_ids`；至少两种实际供应商或协议组合验证后才可标记 `VERIFIED`。缺记录时生成未验证状态，保持发布阻断。

真实供应商、同候选真实评测及受理性能仍有待完成；清理旧报告不代表验收通过。当前结论以本轮输出的 `release-gate.json` 为准。
