# 本地语义检索环境

在服务目录执行以下命令，沿用现有 PostgreSQL 数据卷：

```sh
docker compose --env-file .env -f deploy/compose.dev.yml -f deploy/compose.vector.yml build postgres
docker compose --env-file .env -f deploy/compose.dev.yml -f deploy/compose.vector.yml up -d --wait postgres
.venv/bin/python -m creativity_service.modules.memory.enable_vector
.venv/bin/alembic upgrade head
.venv/bin/pytest tests/integration/memory/test_semantic.py -q
```

镜像编译固定 pgvector 0.8.2 源码并校验 SHA-256；不创建业务函数。扩展由数据库管理员显式启用，业务迁移只建向量缓存表。查询使用扩展所在 schema 的类型和距离算子，在完整主体范围物化后精确排序。未启用扩展时按冻结记忆策略降级或失败。

Agent 需要绑定通过 embedding 能力测试的单模型路由。更换模型版本后重新生成向量，输入用量进入统一运行账本；不会混用不同模型的向量空间。本文件用于开发验证，第 27 号正式部署仍由用户按实际环境处理。
