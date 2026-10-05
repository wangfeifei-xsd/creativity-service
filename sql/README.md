# 数据库初始化 SQL 归档

空库初始化分为两份纯 PostgreSQL SQL，按顺序执行：

| 文件 | 内容 |
| --- | --- |
| [init.sql](init.sql) | 全部应用表与迁移版本表的最终结构、普通索引、中文表和字段注释，不含数据写入 |
| [init_data.sql](init_data.sql) | 系统渠道、菜单、内置角色、角色菜单关联、初始管理员及账号角色关联，最后登记迁移版本 |

文件头记录配套模型版本与迁移基线。数据源 [init_data.json](init_data.json) 冻结当前开发环境的控制面配置，正常生成与检查不重新查询数据库。

## 执行

使用 PostgreSQL 17 的 UTF-8 空数据库，连接账号需能在目标 schema 建表。通过 PostgreSQL 客户端的 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER` 和密码配置连接目标数据库，在服务端目录执行：

```bash
psql -X --set=ON_ERROR_STOP=1 --file=sql/init.sql --file=sql/init_data.sql
```

脚本使用连接的当前 schema，通常为 `public`；如使用其他空 schema，通过 `PGOPTIONS='-c search_path=目标schema'` 指定。文件为纯 PostgreSQL SQL，也可在支持完整脚本与事务的数据库客户端执行，不需要 Python、应用配置或逐个运行历史迁移。

两份文件分别在事务中提交，并共用现有迁移锁。建表存在同名表会报错并回滚本次建表；数据导入失败会整体回滚数据和版本标记，已建结构保留，处理原因后可单独重试数据文件。交互式客户端遇错后执行 `ROLLBACK` 结束失败事务。

**必须完成两份导入后再启动服务或执行迁移。** 不能仅导入表结构后执行 `make migrate`，否则 Alembic 会尝试重新建表。归档只用于新空库，不覆盖已有库；已有数据库继续使用 `make migrate`。数据文件以迁移记录为整批完成标记，重复执行不再插入，也不会重置管理员密码；已有迁移记录的数据库同样不会导入归档数据。

导入成功后，数据库版本已登记到文件头所列修订；`make migrate` 可直接接续后续版本，无需额外 `stamp`。系统渠道已创建，`make channels-init` 再次执行也不会重复创建。

数据归档已创建启用的初始管理员，配置好应用环境与 Redis 后直接使用统一登录入口：

- 登录名：`admin`
- 初始密码：`qwerty123$%^`
- 角色：平台管理员
- 首次登录必须改密；改密前不能执行管理操作。

SQL 和 JSON 中仅保存该密码的 PBKDF2-SHA256 安全摘要，不保存明文密码。默认密码是公开的部署初始凭据，不作为正式环境长期凭据。**此次归档不修改当前环境 admin 的密码或账号关联。** 业务渠道、其他账号、成员与资源授权、Token、业务数据、模型与接入凭据不复制进初始数据；后续通过管理功能配置。归档保留四个不可用于账号分配的历史兼容角色，避免破坏成员角色目录。

当前环境的旧 `admin.role_id` 为空，归档补为 `platform_admin` 并保留其 `platform_roles` 关联；平台管理员的旧动作缺少 `menu:manage`，归档补齐以支持菜单维护。两种管理员的菜单关联按当前菜单与有效动作冻结为 `builtin_roles.menu_ids`，不作为业务渠道的通配授权。导入时间统一取事务时钟，修订和凭据代次从 1 开始。

导入后可执行 `make storage-audit`。Redis 与对象存储由各自部署步骤初始化；可选 pgvector 扩展按 [向量环境说明](../deploy/vector.md) 启用。

## 初始迁移基线

项目尚未上线，原 26 个历史迁移已合并为 [0001_initial.py](../alembic/versions/0001_initial.py)。该文件冻结模型 1.8.0 的最终结构，一次创建 109 张应用表；版本表由迁移环境创建，共 110 张表、1665 个字段、244 个普通索引。基线不读取运行时模型，后续模型修改不会改变已经冻结的建库结果。

初始基线修订号保留为 `0034_admission_indexes`，与合并前最新版本相同。当前最新修订为 `0038_builtin_role_menus`；`0035_management` 新增菜单目录、自定义角色可见菜单清单与管理列表索引，`0036_role_catalog` 将内置角色目录纳入建库初始数据，增加账号选择角色及自定义角色授权类别，`0037_remove_business_type` 删除冗余渠道业务分类列，`0038_builtin_role_menus` 新增内置角色菜单清单。旧内置角色空值继续按有效动作生成导航，增量升级不回填菜单、不修改账号或密码。

空库可使用本目录两份 SQL，或使用 `make migrate`。后者仍使用冻结历史菜单和角色种子，需另执行 `make channels-init`，再用 `uv run creativity-iam init-admin --login-name admin --display-name 管理员` 交互设置初始密码；不应再执行本目录的数据 SQL。开发库执行 `make migrate` 只执行新增修订，不重建既有表。

后续结构变更通过 `uv run alembic revision -m "变更说明"` 新增修订，并同步更新模型和初始化 SQL。不要修改初始基线来替代增量升级。`downgrade base` 会删除全部应用表，只用于明确需要重建的测试库；已有数据的开发库不执行该操作。

早于 `0034_admission_indexes` 的数据库不直接接入压缩后的历史。应先用合并前代码升级至该版本，再使用当前代码；不能仅通过 `stamp` 跳过旧字段及数据迁移。合并前源码、数据库完整备份及核验快照保存在本机 `.local/backups/alembic-baseline-20261005-012534/`，不进入版本控制。模型归档的历史修订来源和历次验收记录保留用于追溯。

## 维护与验证

表结构 SQL 由当前 SQLAlchemy 模型和 Alembic 版本表定义生成；数据 SQL 由 `init_data.json` 和配套迁移修订生成。更新初始配置时修改冻结数据源，再生成 SQL；不要修改已经冻结的历史迁移种子来替代当前环境配置。密码摘要固定保存，不在每次生成时随机更换盐值。开发规范统一见 [rule.md](../../rule.md)，模型与模块归属见 [数据模型索引](../docs/data-model/README.md)。

```bash
make sql
make sql-check
uv run pytest tests/integration/test_init_sql.py -q
```

`make sql` 同时重新生成两份 SQL；生成过程不连接数据库、不加载部署秘密，输出顺序固定。`make sql-check` 同时比较两份完整内容，已纳入 `make check` 和 `make model-check`。

集成测试在真实 PostgreSQL 的独立临时 schema 顺序执行两份归档，对照迁移结构并核验冻结初始数据。验证增量升级与回退、重复导入、建表和数据导入失败回滚、默认密码真实登录及首次改密、管理员角色和菜单关联的运行时读取。测试结束清理自身 schema 和 Redis 前缀。
