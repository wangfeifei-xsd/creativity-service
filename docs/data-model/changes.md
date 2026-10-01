# 模型变更记录

| 版本 / 日期 | 原因及影响 | 代码与迁移 | 兼容及数据处理 |
| --- | --- | --- | --- |
| 1.0.0 / 2026-10-02 | 完成全量 P0 统一设计，98 表及认证 KV；明确 04–25 所属与共享关系 | 公共 10 表：core/database/baseline_v0001.json；Alembic 0001_core；框架版本表沿用 02 | 02 只有空迁移链，无业务数据回填。普通 id、可空物理列与显式服务校验；全部索引为普通索引。业务设计表后续分别迁移，不一次性创建 |

| 1.1.0 / 2026-10-02 | 实现四张 IAM 基线表，账号增加 credential_version；新增 iam_revocations 持久化退出与撤销补偿，Token 增加用途/凭据代次/成员修订/数据域/索引和签发毫秒时间 | modules/iam/baseline_v0002.json；Alembic 0002_iam；应用 storage.py 汇总模型；复用 audit_events | 新建表，无既有 IAM 数据回填。Token 用途严格分离；当前代次拒绝旧授权，索引清理可重试。模型共 99 张逻辑表，16 张已实现（含版本表）、83 张待实现；认证 KV 已实现 |

| 1.2.0 / 2026-10-02 | 实现渠道 8 张基线表；主档增加 business_type、接入服务增加 data_scopes；新增 channel_lifecycle_events 供运行与保留模块消费 | modules/channels/baseline_v0003.json；Alembic 0003_channels；受限摘要索引、真实状态读取器与生命周期服务 | 9 张新表，不改写既有 IAM 或历史业务归属。模型共 100 张逻辑表，25 张已实现（含版本表），75 张待实现；系统渠道由显式命令初始化。最近使用时间不递增 Key 配置 revision；轮换缩短旧 Key 截止时间且保留历史标识 |

后续修改 catalog.json、关系和不变量后重生成模块归档；当前修订的 baseline_v0001.json 永久冻结，新迁移另建修订文件。检查 `make model-check` 对齐实现字段和归档。26 核验实际 PostgreSQL，27 将模型目录连同契约版本冻结成发布快照。
