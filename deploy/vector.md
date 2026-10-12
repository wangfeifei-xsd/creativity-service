# Qdrant 向量环境

MySQL 8 保存业务真值、向量缓存和同步任务，Qdrant 1.19 提供余弦相似度检索。API 与 Worker 使用同一组配置：

| 配置 | 用途 |
| --- | --- |
| `CREATIVITY_QDRANT_URI` | REST 根地址，本机默认 `http://127.0.0.1:6333`，不包含 `/dashboard` |
| `CREATIVITY_QDRANT_API_KEY` | Qdrant API Key；未启用认证的实例可留空 |
| `CREATIVITY_QDRANT_COLLECTION_PREFIX` | 各部署环境独立的集合前缀，例如 `creativity_memory_dev` |
| `CREATIVITY_QDRANT_TIMEOUT_SECONDS` | 请求超时秒数，默认 10 |

dev 和 test 共用 `http://8.155.161.218:36333`，集合前缀分别为 `creativity_memory_dev` 和 `creativity_memory_test`，在同一实例内隔离。连接地址维护在 `.env.dev` 和 `.env.test`；prod 使用独立配置。

现有远端实例直接在对应环境文件或部署变量中配置，无需运行本地容器。健康检查仅验证连接，不创建集合。需要独立本地实例时执行：

```bash
docker compose --env-file .env.dev -f deploy/compose.dev.yml -f deploy/compose.vector.yml up -d --wait qdrant
uv run python -m creativity_service.modules.memory.enable_vector
```

本地 Compose 固定 `qdrant/qdrant:v1.19.1`，使用独立持久卷；REST 端口只绑定回环地址，默认 6333，可通过 `DEV_QDRANT_PORT` 修改。连接该实例时将 `CREATIVITY_QDRANT_URI` 设置为相应本机地址。API Key 同步注入容器。启动脚本在容器运行后通过 REST 验证就绪。

首次写入按实际向量维度自动创建集合，名称为 `<集合前缀>_d<维度>`，距离为 `Cosine`。已有集合必须与维度和距离一致。自动创建 `channel_id` 的 keyword 租户索引，以及 `environment`、`subject_type`、`subject_id`、`model_version_id`、`id` 的 keyword 索引；可在 Qdrant Dashboard 的 Collections 页面核对。

MySQL 中的缓存标识保持原样，Qdrant 点 ID 由完整主体范围和缓存标识稳定生成 UUID，payload 保存原标识和来源引用，不保存记忆原文。每次搜索在 Qdrant 内同时限制渠道、环境、主体类型、主体编号、模型版本和当前有效标识；返回后在新的 MySQL 事务内批量复核授权、偏好开关、来源、版本和状态。删除同样限定完整主体范围与缓存标识。写入和删除使用 `wait=true`、`ordering=strong` 并要求服务端确认完成，查询使用 `consistency=all`。

`memory_embeddings` 保留可重建的向量缓存；`memory_index_tasks` 保存写入/删除意图、租约和重试时间。缓存及任务在同一 MySQL 事务提交；网络调用在事务外执行。首次语义读取尝试同步本批缓存，Worker 每分钟继续处理失败和过期租约。Qdrant 不可用或索引尚未就绪时，通过既有记忆失败策略降级或失败。

遗忘先在 MySQL 和独立删除清单建立屏障，立即停止召回；清理任务持久化 DELETE 意图，待 Qdrant 删除确认后清除缓存。删除意图不可改回写入，墓碑不包含向量或记忆原文，并定期再次清理，覆盖进程崩溃及迟到写入。来源删除和恢复屏障同样参与同步前及召回后复核。

从旧向量后端切换、Qdrant 数据丢失或更换集合前缀后，先升级 MySQL 迁移，再从缓存按业务渠道重建：

```bash
uv run alembic upgrade head
uv run python -m scripts.rebuild_memory_vectors --channel 渠道标识
```

重建将既有有效缓存的同步任务重新置为待处理，Worker 按同一协议写入；没有缓存时由首次语义读取创建集合和索引。切换前停止旧 API/Worker，配置和迁移完成后启动新进程，避免已同步状态仍指向旧后端。旧向量实例及其持久卷由运维另行保留或清理。

更换 embedding 模型或向量空间时必须使用新的模型版本/路由摘要，由正常语义读取生成新向量，不能将旧模型的向量复制到新空间。
