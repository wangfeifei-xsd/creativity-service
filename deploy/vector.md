# Milvus 向量环境

当前使用 MySQL 8 保存业务真值、向量缓存和同步任务，Milvus 2.6 提供余弦相似度检索。API 与 Worker 使用同一组 `CREATIVITY_MILVUS_*` 配置；连接地址为 REST v2 地址，默认 `http://127.0.0.1:19530`。认证 Token 由部署环境或 `.env.local` 注入。

```bash
docker compose --env-file .env -f deploy/compose.dev.yml -f deploy/compose.vector.yml up -d --wait milvus
uv run python -m creativity_service.modules.memory.enable_vector
```

本地 Compose 固定 Milvus `v2.6.3`，使用嵌入式 etcd、本地存储和独立持久卷，端口仅绑定回环地址。该单机配置用于开发；生产由运维提供具备所需可用性、认证与备份能力的 Milvus 部署。

集合按实际向量维度创建，格式为 `creativity_memory_d维度`，使用 AUTOINDEX 和 COSINE。写入标识由完整主体范围、记忆版本、模型版本和路由内容摘要生成。每次搜索在 Milvus 内同时限制渠道、环境、主体类型、主体编号、模型版本和当前有效标识；服务收到引用后，在新的 MySQL 事务内批量复核授权、偏好开关、来源、版本和状态。

`memory_embeddings` 保留可重建的向量缓存；`memory_index_tasks` 保存写入/删除意图、租约和重试时间。缓存及任务在同一 MySQL 事务提交；网络调用在事务外执行。首次语义读取尝试同步本批缓存，Worker 每分钟继续处理失败和过期租约。Milvus 不可用或索引尚未就绪时，通过既有记忆失败策略降级或失败，不伪造成功结果。

遗忘先在 MySQL 和独立删除清单建立屏障，立即停止召回；清理任务持久化 DELETE 意图，待 Milvus 删除确认后清除缓存。删除意图不可改回写入，墓碑不包含向量或记忆原文，并定期再次清理，覆盖进程崩溃及迟到写入。来源删除和恢复屏障同样参与同步前及召回后复核。

Milvus 数据丢失或切换集合前缀后，可以从 MySQL 缓存按渠道重建：

```bash
uv run python -m scripts.rebuild_memory_vectors --channel 渠道标识
```

重建仅重新登记有效缓存的同步任务；后台按同一协议写入。更换 embedding 模型或向量空间时必须使用新的模型版本/路由摘要，由正常语义读取生成新向量，不能将旧模型的向量复制到新空间。
