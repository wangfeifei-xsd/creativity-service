# 11 运行受理与调度验证记录

验证日期：2026-10-02。运行服务与边界见 [交接说明](runs.md)。本轮使用本机 Python 3.12 虚拟环境、真实 PostgreSQL 独立 schema、Redis 与 Celery 测试 Worker；测试结束清理临时数据库。未对业务数据库执行升级。

## 自动验证结果

| 检查 | 结果 |
| --- | --- |
| `pytest tests/integration/runs -q` | 26 项通过 |
| `pytest -m 'not integration' -q` | 全工程 126 项通过；1 条来自技能 ZIP 重复文件名拒绝测试的预期警告 |
| `ruff format --check .` / `ruff check .` | 全工程通过 |
| `mypy` | 198 个源文件通过 |
| 运行契约、公共契约与主 OpenAPI `--check` | 通过 |
| `scripts/render_data_model.py --check` / 存储定义与源码审查 | 通过 |
| 临时 schema 升级到 head → 降级到 0010 → 再升级，逐次实际数据库审查 | 通过，中文注释与索引/字段和元数据一致 |
| Alembic heads | 单一 `0019_parallel_runs` |

调用上述命令使用 `.venv/bin/pytest`、`.venv/bin/ruff`、`.venv/bin/mypy`、`.venv/bin/python`，避免依赖当前 shell 未提供的 uv 命令。全量回归曾在并行模块更新中遇到契约和工具测试夹具不同步；交付前复核已全部通过。

## RUN 验收映射

| 编号 | 验证内容与证据 |
| --- | --- |
| RUN-A01 | 8 个并发相同请求只创建一个 run、快照及 outbox；切换 Key、返回方式或重启受理端重放均命中原运行；异内容拒绝；原幂等到期时间已过但运行未终结仍返回原运行 |
| RUN-A02 | 队列发布异常保留 QUEUED 与脱敏失败类别，补偿恢复投递；真实 Redis/Celery 收到重复渠道消息 |
| RUN-A03 | 两个 Worker 并发认领仅一份有效租约；失效租约提交被拒绝；新租约代次递增 |
| RUN-A05 | 分别控制成功先提交、取消先接受，另并发竞争四轮；迟到结果不改终态，completed 只登记一次 |
| RUN-A06 | 模型返回后独立进程 `os._exit(9)`，租约失效后原 Attempt 为 UNKNOWN，费用保持 MISSING/PENDING；晚到供应商用量补记不改变 FAILED；已保存返回内容可由新租约复用，不新增模型调用 |
| RUN-A07 | 超时、授权失效、删除标记与取消的运行均不能继续恢复；排队时间计入 deadline；真实 IAM/Redis 登录 Token 到期后客户端访问拒绝，持久化 Worker 身份仍能在当前授权下完成 |
| RUN-A08 | 管理 rerun 创建独立 run 与新准入，parent_run_id 指向原运行；按当前输出结构校验，不合法结果不得成功 |
| RUN-A09 | 首次受理、会话争用、重复 checkpoint、双 Worker 与取消竞争；步骤/事件递增，重复 checkpoint 不能改写已保存内容；预算或会话钩子失败没有残留消息、占用或投递意图 |
| RUN-A11 | 同一 Worker 连续处理两个渠道同名 Agent 和节点，消息重复不重复执行，checkpoint 各归原渠道；缺少/伪造渠道拒绝；每次上下文清理；管理查询主体范围来自存储且不能跨数据域 |

## 故障窗口

| 注入位置 | 持久化观察 | 补偿结果 |
| --- | --- | --- |
| `admission_committed_before_publish` | run、幂等、快照、准入与 PENDING outbox 已提交 | 重放返回原 run，dispatcher 扫描后投递 |
| `published_before_confirmation` | 消息已发出，outbox 仍为 PUBLISHING | 声明到期重新投递；两份消息只取得一个有效租约 |
| 远端返回后未保存 | 独立子进程已提交发送意图，收到内部模拟返回后硬退出 | 原模型 Attempt 标记 UNKNOWN，保留账本预占，停止自动再调用；不把缺失费用记成零 |
| `terminal_committed_before_release` | SUCCEEDED、result/completed 已提交，resources_released=false | 恢复扫描幂等释放准入与会话占用；重复扫描不重复释放或登记完成事件 |

其余边界覆盖：3 次只读工具尝试后禁止第 4 次；并发追加事件保持连续单调序号；过期事件返回 EVENTS_EXPIRED 后仍能查询状态；未安装正式解析器时受理返回 503，上传执行描述返回 422；管理列表、查询、取消、rerun 和轨迹端口可由 17 装配。

## 后续组合验证

本轮定义解析与版本发布验证使用测试目录内的内部夹具；实际模型和业务调用未作真实供应商联调。17 继续验证生产解析器、当前主体权限、会话 12、模型/工具计量、LangGraph checkpoint_writes、SSE/同步等待与页面。25 承担最终保留清理及跨派生对象删除传播。上述组合验收不由本次模拟外部返回替代。
