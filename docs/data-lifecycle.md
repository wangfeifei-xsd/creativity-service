# 删除传播与数据保留

方案 25 交接，2026-10-03。实现入口为 `modules/data_lifecycle/`、`workers/cleanup/`；验证范围见 [验收记录](data-lifecycle-validation.md)。通用规范继续引用 [项目规则](../../rule.md)。

## 删除链与接口

继续使用公共 `deletion_markers`、`source_links`、`recovery_barriers` 和渠道内容图锁。会话、记忆及统一删除入口先向独立卷持久化意图，再提交数据库标记。查询、产物下载、内容提交、工具缓存和 checkpoint 恢复都核查同一机制；清理失败不会解除标记。

`deletion_jobs` 沿用会话模块已创建的共享任务表；迁移 `0025_data_lifecycle` 只增加 `deletion_work_items` 和 `deletion_receipts`。工作项保存原始范围、处理器、租约、重试次数和脱敏错误码。默认每批最多 100 项，可配置到 500 项；租约 5 分钟，失败指数退避，最多间隔 1 小时。重复投递、失联租约和进程退出均由持久化任务恢复。

以下路径同时提供 `/admin/v1` 与 `/api/v1` 前缀。调用身份、渠道、环境、数据域和主体由服务器恢复与授权，正文不接受范围覆盖。

| 方法与路径 | 行为 |
| --- | --- |
| `POST /data-lifecycle/deletion-preview` | 正文为 `resource_type` 与 `resource_id`，按类型返回影响数量 |
| `POST /data-lifecycle/deletions` | 立即登记不可恢复使用标记，返回 202、任务编号及步骤进度 |
| `GET /deletions/{deletion_id}/progress` | 按类型返回完成数、失败数及完成证明摘要；兼容会话和记忆任务编号 |
| `POST /deletions/{deletion_id}/retry` | 重排失败步骤，已完成步骤保持幂等 |

统一入口支持会话、消息、记忆、运行、工具调用和文件。会话删除和清空记忆保留原有入口。关闭长期记忆只控制读写偏好，资源停用和会话归档只控制状态；它们均不代替删除。

前端共用 `DeletionSteps` 展示分类型进度与失败重试，运行详情使用 `ContentDeletion` 预览并确认，提交后清除当前显示的原文。渠道表单提供各项保留周期。

## 内容覆盖与多来源

| 内容 | 清理行为 |
| --- | --- |
| 会话、消息、摘要、上下文 | 撤销读取并取消相关运行，清除消息、摘要、上下文和轮次输入 |
| 运行 | 清除输入输出、实际模型/工具输入、内容事件、checkpoint、部分响应和 Attempt 错误原文；保留执行状态及计费关系 |
| 记忆 | 删除失效来源、历史值和检索副本；仍有独立有效来源时重算；来源边移除前固化撤销标记 |
| 工具、证据、文件 | 清除调用参数/响应摘要/错误及证据位置，删除对象；迟到上传再次核查标记并回收对象 |
| 样本、夹具、评测、调试 | 清空失效样本、夹具、判断和报告原文；报告标记不可复现；不沿汇总报告删除其他独立样本的运行 |
| 缓存与导出 | 删除工具内容缓存及关联导出；缓存复用登记来源运行，读取和写后复核来源；定期回收无有效元数据的渠道对象 |

来源图按渠道遍历，并用每条记录自己的 Scope 清理，覆盖主体反馈形成管理样本的跨主体关系。主体范围删除只选择匹配的个人/环境记录，共享渠道配置不因缺少主体列而被误选。输入消息与工具返回已进入运行副本，即使上下文来源边尚未形成，删除检查也会阻断关联运行。

评测默认 `source_mode=all`，来源不足即撤销。显式 `independent` 仅接受输入输出摘要完全等价的完整成功运行，样本输入须等于每个来源的完整脱敏输入，并有实际匹配结果的等值或数值断言；混合上下文与工具夹具不接受独立模式。删除后保留存活依据与可独立支持的样本，清空自由标签、人工理由、旧标题与断言名称；当前来源授权仍由读取/发布入口复核。

## 保留策略与调度

| 策略 | 默认值 | 实际处理 |
| --- | --- | --- |
| `retention_days` | 90 天 | 会话；归档渠道的共享配置内容 |
| `run_content_days` | 30 天 | 运行及一般文件内容；显式到期时间可更早 |
| `metadata_days` | 365 天 | 审计、用量汇总、已结算/已释放账本及关联事件、修正、预占 |
| `sse_hours` | 24 小时 | SSE 事件及事件内容；不删除仍在保留期内的完整运行输入输出 |
| `temporary_hours` | 1 小时 | 暂存/失败上传及无有效元数据的对象 |
| `export_days` | 7 天 | 普通渠道导出；显式到期时间可更早 |

记忆按自身策略的 `expires_at` 处理，不套用运行的 30 天周期。暂停不停止到期扫描；归档后按原渠道策略到期清理。尚待结算用量不因元数据期限被删除，迟到用量只接收计量数字，不保存任意响应或诊断字符串。删除标记、完成证明和恢复屏障不自动过期。

部署须同时运行 Worker 与 `make scheduler`；Celery Beat 每 30 秒唤醒 `cleanup.sweep`。Worker 从服务端渠道目录获取渠道，扫描政策并消费 `channel_lifecycle_events` 的 `retention` 进度，操作人权限撤销或渠道暂停不撤销已受理的清理义务。单次人工扫描：

```bash
uv run creativity-lifecycle sweep --channel CHANNEL_ID
```

## 独立清单与恢复交接

`CREATIVITY_DELETION_LEDGER_PATH` 默认 `.local/deletion-ledger`，仅供本地开发。生产必须挂载独立持久卷，所有 API/Worker 共享且支持进程间文件锁；不能跟随旧数据库或对象备份一起回滚。每渠道目录按渠道摘要命名，清单原子替换、校验和、文件及目录 fsync 保证意图先落盘。此机制依赖持久卷及其备份，27 负责部署拓扑、最新水位保管、容灾复制和 RPO/RTO。

初次升级时，在尚未回滚的当前数据库上逐渠道执行 `backup`，将现有标记汇入独立清单并记录水位，再启动定时清理。新空渠道自动初始化独立清单；已有数据的清单丢失或损坏会拒绝内容入口。不得用已经回滚的旧数据库重新生成一个空清单。

```bash
# 在当前数据仍可信时导出独立清单，并同时备份独立卷。
uv run creativity-lifecycle backup --channel CHANNEL_ID --output deletion-manifest.json

# 恢复旧数据库或对象之前，先持久化关闭入口。
uv run creativity-lifecycle restore-block --channel CHANNEL_ID
uv run creativity-lifecycle restore-check --channel CHANNEL_ID --minimum-sequence LATEST_SEQUENCE

# 排空在途请求并停止内容 Worker，再执行数据库与对象恢复；独立清单卷保持最新版本。
# 若独立卷也损坏，先从单独备份恢复该卷，不能用旧业务备份替代。
uv run creativity-lifecycle restore-replay --channel CHANNEL_ID --minimum-sequence LATEST_SEQUENCE --output cleanup-proof.json
```

`LATEST_SEQUENCE` 取最新独立清单备份的 `manifest.sequence`，须不早于已确认删除事件；恢复命令必须显式提供该值。`restore-check` 在数据库恢复前即可执行。`restore-replay` 导入最新标记，重排旧任务、清理旧数据库内容与对象、收集完成证明，再在同一文件锁内比对水位与清单状态摘要并开放。缺失/损坏清单、水位不足、任务失败、中途产生新删除事件或新的恢复封锁均阻止开放；修复原因后重复重放。

`backup` 输出 `creativity-deletion-manifest-v1`：包含 `observed_at`、`manifest` 和该清单的 `digest`。清单包含渠道、递增水位、封锁状态、删除目标/范围/操作人/原因/登记时间，不含原文。独立卷仍是恢复执行所读取的真值，JSON 导出用于独立备份核对与交接。

每个数据库完成证明包含 `job_id`、`marker_digest`（受影响资源集合摘要）、按类型数量、完成时间与 `proof_digest`。恢复输出 `creativity-cleanup-proof-v1`：渠道、核对水位、清单摘要、各任务证明摘要和核对时间。27 应将清单快照、完成证明及发布版本一并保存。证明表示受登记来源覆盖的内容已清理，不表示业务系统自行存储的数据也被删除。
