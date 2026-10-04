# 受理优化验证材料

最终结论见 [修复报告](../../admission-optimization-20261005.md)。本目录的性能采样和 SQL 探针单独执行，不能把带探针的时延当成正式验收值。

- `performance.json`、`performance.log`：完整回归后的无探针复测，固定 20 并发、每类 20 次预热和 100 次正式采样，模型调用为 0。
- `lock-profile.json`、`profile-environment.json`、`profile-summary.json`、`profile.log`：同一最终源码的独立 20 请求诊断，记录 SQL、锁和阶段耗时。阶段存在包含关系，不能相加；SQL await 时间包含客户端调度和网络等待，不是数据库纯执行时间。
- `integration.xml`、`integration.log`：完整集成回归，包括通过、失败及跳过原因。
- `backend-check.log`、`frontend-check.log`、`browser.log`：后端检查、前端检查与 6 项浏览器用例。浏览器用例使用路由夹具。
- `channel-concurrency.png`、`platform-concurrency.png`：浏览器用例采集的页面截图。
- `verification.json`：源码、迁移、前端和测试脚本摘要，以及测试边界与最终验收状态。
- `pre-regression-performance.*`：回归前的中间测量，保留用于审计，不代替最终结果。

在 `creativity-service` 根目录运行，先确保没有其他测试进程或源码修改任务。两个命令依次执行，各自会更新对应证据文件：

```sh
PYTHONPATH="$PWD" .venv/bin/pytest -c pyproject.toml \
  docs/acceptance/admission-optimization-20261005/test_timing.py \
  -q -s --tb=short \
  > docs/acceptance/admission-optimization-20261005/performance.log 2>&1
```

```sh
PYTHONPATH="$PWD" .venv/bin/pytest -c pyproject.toml \
  docs/acceptance/admission-optimization-20261005/test_lock_profile.py \
  -q -s --tb=short \
  > docs/acceptance/admission-optimization-20261005/profile.log 2>&1
```

两者使用正式集成测试的数据准备、真实本地 PostgreSQL 和 Redis，并创建隔离 schema。无探针脚本只替换结果保存位置；负载、预热、采样、事件循环及 500 ms 断言均由原验收函数决定。SQL 计数使用 SQLAlchemy 执行事件，不包含驱动自动事务语句，批量参数与一条 CTE 内的多次 INSERT 不按记录数重复计数。
