# 数据库初始化 SQL 归档

[init.sql](init.sql) 是当前已实现功能的完整空库初始化文件，直接创建最终表结构。文件头记录模型版本、Alembic 基线、表、字段和索引数量。归档包含全部已登记应用表、迁移版本表、普通索引、中文表与字段注释，以及平台系统渠道初始记录。

## 执行

使用 PostgreSQL 17 的 UTF-8 空数据库，连接账号需能在目标 schema 建表。通过 PostgreSQL 客户端的 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER` 和密码配置连接目标数据库，在服务端目录执行：

```bash
psql -X --set=ON_ERROR_STOP=1 --file=sql/init.sql
```

脚本使用连接的当前 schema，通常为 `public`；如使用其他空 schema，通过 `PGOPTIONS='-c search_path=目标schema'` 指定。文件为纯 PostgreSQL SQL，也可在支持完整脚本与事务的数据库客户端执行，不需要 Python、应用配置或逐个运行历史迁移。

建表、索引、注释、系统渠道及版本记录在同一事务提交，并使用现有迁移锁。存在同名表会报错并回滚本次执行；不要用于覆盖已有库。交互式客户端遇错后执行 `ROLLBACK` 结束失败事务。已有数据库继续使用 `make migrate`。

导入成功后，数据库版本已登记到文件头所列修订；`make migrate` 可直接接续后续版本，无需额外 `stamp`。系统渠道已创建，`make channels-init` 再次执行也不会重复创建。

配置好应用环境与 Redis 后，使用现有服务命令初始化管理员及内置角色：

```bash
uv run creativity-iam init-admin --login-name admin --display-name 管理员
make storage-audit
```

密码由终端交互输入，首次登录须改密。业务渠道、模型、MCP 连接和凭据通过管理功能配置。Redis 与对象存储由各自部署步骤初始化；可选 pgvector 扩展按 [向量环境说明](../deploy/vector.md) 启用。

## 初始迁移基线

项目尚未上线，原 26 个历史迁移已合并为 [0001_initial.py](../alembic/versions/0001_initial.py)。该文件冻结模型 1.8.0 的最终结构，一次创建 109 张应用表；版本表由迁移环境创建，共 110 张表、1665 个字段、244 个普通索引。基线不读取运行时模型，后续模型修改不会改变已经冻结的建库结果。

修订号保留为 `0034_admission_indexes`，与合并前最新版本相同。已到该版本的开发库执行 `make migrate` 会直接识别现有结构，不重建表、不修改版本记录，也不重复执行旧数据回填。空库可使用 `make migrate` 或本目录的 `init.sql`；前者仍由 `make channels-init` 初始化系统渠道。

后续结构变更通过 `uv run alembic revision -m "变更说明"` 新增修订，并同步更新模型和初始化 SQL。不要修改初始基线来替代增量升级。`downgrade base` 会删除全部应用表，只用于明确需要重建的测试库；已有数据的开发库不执行该操作。

早于 `0034_admission_indexes` 的数据库不直接接入压缩后的历史。应先用合并前代码升级至该版本，再使用当前代码；不能仅通过 `stamp` 跳过旧字段及数据迁移。合并前源码、数据库完整备份及核验快照保存在本机 `.local/backups/alembic-baseline-20261005-012534/`，不进入版本控制。模型归档的历史修订来源和历次验收记录保留用于追溯。

## 维护与验证

SQL 由当前 SQLAlchemy 模型、Alembic 版本表定义及系统渠道初始配置共同生成。开发规范统一见 [rule.md](../../rule.md)，模型与模块归属见 [数据模型索引](../docs/data-model/README.md)。

```bash
make sql
make sql-check
uv run pytest tests/integration/test_init_sql.py -q
```

`make sql` 重新生成并覆盖归档文件；生成过程不连接数据库、不加载部署秘密，输出顺序固定。`make sql-check` 比较完整内容，已纳入 `make check` 和 `make model-check`。

集成测试在真实 PostgreSQL 的独立临时 schema 执行归档，对照初始基线及后续迁移的表、字段、类型、空值属性、注释和索引，运行存储审查，并临时追加字段迁移验证升级与回退时系统渠道保持完整。同时验证重复导入、部分建表失败时的事务回滚，以及系统渠道与管理员初始化登录。测试结束清理自身 schema 和 Redis 前缀。
