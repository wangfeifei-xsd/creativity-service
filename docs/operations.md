# 费用、评测与数据维护

<a id="usage"></a>

## 费用与预算

模型价格、预算、预占和结算统一由用量模块维护。每次实际模型尝试独立记账，调试与评测也进入同一账本。管理端查看 `/usage/summary`、`/usage/records`、`/budgets` 和模型价格版本；平台汇总需要对应平台权限。

运行先准入，再为实际尝试预占预算、保存发送意图，最后按供应商事件结算。缺失用量、未知调用和未定价分别展示；取消、断流或超时不能证明费用为零。重试有新的 Attempt，迟到用量归回原尝试，修正追加依据。

平台与渠道限额共同控制准入；排队占用不等于模型实际并发。计量维度和字段见 [用量模型](data-model/modules/usage.md)。

<a id="evaluations"></a>

## 评测与发布

准备样本、标签、断言和工具夹具，固定数据集版本及 Agent 候选，再创建评测。JSONL/CSV 导入先预览并检查错误，携带预览摘要提交。运行反馈转样本需审阅和脱敏，具体结构见 [评测契约](../contracts/evaluations/)。

报告分别列出通过、失败、无效、未执行与取消，并保留每次重跑和费用。确定性失败、授权、泄密、伪造引用及计算错误不能被平均分或人工评分覆盖。评测默认禁止真实写工具。

正式发布需要适用的固定数据报告、完整样本结果、阈值和人工审阅；后续发布还要与当前发布基线比较。候选内容、依赖、样本或来源变更后重新校验。具体门禁见 [发布检查](../src/creativity_service/modules/releases/evaluation.py)，可运行样例见 [评测示例](../examples/evaluations/README.md)。

<a id="data-lifecycle"></a>

## 删除与保留

删除先持久化独立意图及不可用标记，再异步清理会话、运行内容、记忆、工具证据、文件、样本和缓存。任务失败可重试，标记仍继续阻断读取与恢复；归档、停用和关闭记忆不等于删除。

| 接口 | 用途 |
| --- | --- |
| `POST /data-lifecycle/deletion-preview` | 查看资源删除影响 |
| `POST /data-lifecycle/deletions` | 登记删除并返回 202 |
| `GET /deletions/{id}/progress` | 查看分类型进度与证明 |
| `POST /deletions/{id}/retry` | 重排失败步骤 |

上述接口分别提供 `/admin/v1` 与 `/api/v1` 前缀。Worker 与 Beat 必须运行；人工触发扫描使用 `uv run creativity-lifecycle sweep --channel CHANNEL_ID`。

默认保留期：会话 90 天，运行内容 30 天，元数据 365 天，SSE 24 小时，临时对象 1 小时，导出 7 天；渠道策略可配置，记忆使用自身有效期。待结算用量、删除标记和恢复屏障有独立保留规则。

## 备份与恢复

`CREATIVITY_DELETION_LEDGER_PATH` 在本地默认 `.local/deletion-ledger`。部署时所有 API/Worker 共用支持进程间锁的独立持久卷，单独备份，恢复旧业务数据库时保留最新删除清单。

按顺序执行，`LATEST_SEQUENCE` 取最新独立清单备份的 `manifest.sequence`：

```bash
# 在当前数据仍可信时导出清单，并备份独立卷。
uv run creativity-lifecycle backup --channel CHANNEL_ID --output deletion-manifest.json

# 恢复旧业务备份前先关闭内容入口并核对水位。
uv run creativity-lifecycle restore-block --channel CHANNEL_ID
uv run creativity-lifecycle restore-check --channel CHANNEL_ID --minimum-sequence LATEST_SEQUENCE

# 排空请求、停止内容 Worker，再恢复数据库与对象；独立清单卷保持最新。
uv run creativity-lifecycle restore-replay --channel CHANNEL_ID --minimum-sequence LATEST_SEQUENCE --output cleanup-proof.json
```

已有内容而清单丢失时不能从旧数据库重新生成空清单。缺失、水位不足、清理失败或出现新删除意图都会阻止开放。清单和证明格式见 [生命周期数据结构](../src/creativity_service/modules/data_lifecycle/schemas.py)。
