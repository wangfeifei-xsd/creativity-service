# 存储职责与清理映射

模型版本 1.7.0；物理字段见 [索引](README.md)。

| 存储 | 内容与键/路径 | TTL / 保留 | 清理及恢复关系 |
| --- | --- | --- | --- |
| PostgreSQL 公共配置 | resource_versions / resource_references / release_mappings | 引用存续期间保留 | 正常发布不可改内容；个人删除可清空正文并标记退役，保留无原文摘要和版本关系 |
| PostgreSQL 身份与配置 | 04–10、14–16、18 的设计表 | 状态与业务保留策略 | 密钥撤销保留历史身份；不保存 Token 明文 |
| PostgreSQL 运行、快照及工具内容 | 11/17 + release_snapshots / evidence_refs | 输入输出建议 30 天 | 完整 Scope 标记先阻断，再清理 run、checkpoint、样本及派生产物 |
| PostgreSQL 会话 / 记忆 | 12/13 的设计表 | 会话 90 天；明确偏好建议 180 天后确认；其余按策略 | 原文删除传播到摘要、来源与记忆版本；有独立来源由服务重算 |
| PostgreSQL 账本与审计 | usage_* / budget_* / audit_events | 元数据建议 365 天 | 不保留敏感原文；按币种和完整性分组；删除不抹消耗事实 |
| PostgreSQL 删除与恢复 | deletion_markers / recovery_barriers / deletion_jobs / deletion_work_items / deletion_receipts | 标记至少跨所有备份/派生数据存续周期，P0 不自动过期 | 标记进入独立备份账本；恢复先封锁、导入外部标记、核对摘要，再开放 |
| 独立删除清单卷 | `CREATIVITY_DELETION_LEDGER_PATH/{渠道摘要}/manifest.json` | 不自动过期，与业务备份分离 | 原子落盘意图与恢复封锁；导出最新水位，清理重放证明核对后开放 |
| Redis 认证库 | `creativity:auth:token:{token_digest}`；值字段见账号模型 | 管理建议 7200 秒；服务建议 3600 秒且不晚于 Key 到期；单次 Lua 内 SET PX，固定到期 | 摘要 ZSET 索引按系统账号、目标渠道成员、目标渠道 Key 建立，TTL 为索引内最晚到期；复核当前状态，不能仅信索引；无 PostgreSQL 第二份真值 |
| Redis 缓存库 | `creativity:{namespace}:{channel}:{environment}:{digest(Scope, parts)}` | 各模块显式指定 TTL；工具新鲜度须短于来源时效 | parts 包含工具版本、授权摘要、参数摘要；工具缓存带来源运行，按渠道失效且读取再查来源标记；不可跨范围回退 |
| Redis 限速/委托防重放 | 与 scoped_key 同协议，身份未确定的失败归 system 专用服务键 | 限流周期或委托剩余时长，必须设 TTL | 仅由 04/18 受限入口维护，不构成业务权限 |
| Celery broker/result | 独立 Redis 库及部署前缀；业务消息含 channel_id / run_id | 队列 TTL 不决定 run 有效性；业务结果后端关闭 | PostgreSQL outbox、deadline、租约为事实；每个 Worker 消息独立加载上下文并清理 |
| 私有对象存储 | `channels/{channel}/{environment}/{Scope摘要}/staging/{artifact_id}` | 暂存登记窗口 1 小时；可用文件显式 expires_at，建议 30 天 | 路径即私有对象键，登记后仍由元数据控制；永久公开地址/预签名下载不对外提供；过期暂存和失败上传反复回收 |
| LangGraph checkpoint | 自有 checkpoints / checkpoint_writes，按 channel/run/namespace/key | 不超过源内容与运行保存期 | 17 以公共 UoW/锁/删除检查实现适配；不启用框架自动 DDL、默认 PK 或 upsert |
| 后续检索索引 | channel / environment / data_scope / subject_type / subject_id 分区，带 source/version | P1 再启用，不能长于源内容 | 查询先范围过滤；删除撤掉所有派生向量与缓存；恢复需过同一屏障 |

共用内容清理类型 `artifact`、`version`、`snapshot` 由 03 登记。未来模块启用含内容表时，应同步登记处理器，方案 25 从机器清单的 content 字段汇总完整覆盖清单。存在表而未登记处理器必须在集成装配检查中报错。首次范围初始化只适用于没有历史内容的数据空间；恢复已存在的范围必须使用独立账本校验协议。

系统渠道的跨渠道汇总是独立平台能力，须记录确切 channel_range。普通业务仓储不接收 ControlScope；控制面仓储只接受登记表、用途及精确键查询。价格账本、成员授权、业务域映射均只有所属模块的一份真值。

04 认证索引采用 `creativity:auth:index:{channel_id}:{account|member|key}:{id}`，账号索引归 system，成员和 Key 索引归各业务渠道。登录限速键为 `creativity:auth:limit:system:{login|ip}:{摘要}`，TTL 300 秒。Token KV 必含 channel_id；使用固定期限且不自动续期。PostgreSQL 的 iam_revocations 保存补偿元数据及退出意图，不保存 Token 明文，也不代替 Redis 会话真值。

25 已实现默认运行 30 天、会话 90 天、元数据 365 天，以及 SSE 24 小时、暂存/孤儿对象 1 小时、普通导出 7 天。记忆使用自身到期时间；待结算用量受保护。具体覆盖与恢复命令见 [生命周期交接](../operations.md#data-lifecycle)。系统控制面的汇总导出仍由用量模块自身授权及到期任务管理，不从业务渠道扫描跨渠道清理。
