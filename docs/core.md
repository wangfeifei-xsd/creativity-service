# 03 公共设施交接

数据模型基线与契约版本均为 **1.0.0**。需求、技术及规则来源见 [方案 03](../../代码编写执行方案/03-公共服务与数据契约.md)。完整模型在 [统一归档](data-model/README.md)，验证证据见 [验证记录](core-validation.md)，共享类型与兼容策略见 [契约说明](../contracts/README.md)。

## 实现入口

| 入口 | 已交付内容 |
| --- | --- |
| `core/context` | 不可变 Scope/AuthContext、受限 ControlScope、HTTP 注入、当前渠道状态端口、独立后台上下文 |
| `core/database` / `core/locking` | SQLAlchemy Core 定义、显式范围仓储、工作单元、稳定 PostgreSQL advisory lock 与多键排序、revision 冲突 |
| `core/primitives` / `core/contracts` | 随机 ID、UTC 时间、金额、签名分页、语义摘要、全部固定交接类型及生成器 |
| `core/versioning` | 草稿编辑、内容冻结、引用登记、环境映射切换、调用方事务内的快照、受保护版本/快照读取 |
| `core/security` | 密钥版本抽象、AES-GCM 凭据存储及调用边界、分用途出站目标/重定向/头校验、完整范围键和游标 |
| `core/artifacts` | 有界私有 S3 上传/下载、暂存登记、来源关系、授权与删除二次检查、失败暂存回收 |
| `core/deletion` | 删除标记、祖先来源检查、恢复屏障、独立账本证明端口、公共内容清理处理器登记 |
| `core/observability` | 渠道及环境日志关联、事务内无原文审计；原有 API/Worker 追踪保持共用 |
| `core/services.py` | 公共服务装配，缺少授权、版本门禁或密钥实现时拒绝敏感操作 |
| 前端 `src/api` / `src/components` | 生成类型、统一错误码/字段错误、十进制金额与日期展示、版本选择、状态标签、服务端动作按钮 |
| 前端 `src/features/navigation.ts` | 服务端导航键映射登记组件，未知键不产生路由；不计算角色或 Token 有效性 |

本次新增 `0001_core` 修订及 10 张公共表；Alembic 版本表沿用 02 的受限存储。普通 id 不建立主键或唯一索引；仓储在记录锁内按渠道检查 id。公共元数据定义与冻结迁移字段文件一致；后续修改当前模型需新增冻结定义及迁移，不能覆盖 `baseline_v0001.json`。开发启动不自动执行 DDL。

## 接入顺序

1. 04 实现 AuthenticationResolver 与 Authorization。05 实现 ChannelStateReader，18 实现委托身份；通过服务端验证形成 AuthContext。HTTP 上下文携带服务端核准的 session_id 与 token_digest，Authorization 在每次文件交付边界复核 Redis TTL；后台上下文清除此访问会话凭据，只复核运行所绑定的当前身份和授权。普通业务正文不得作为 AuthContext 解析，客户端和模型都不能改渠道。
2. 在应用生命周期构造 `build_core_services(engine, store, authorization, version_validator, key_provider)`。默认装配全部敏感入口拒绝；不要给生产补“允许全部”替身。后台任务用 `task_context(message, persisted_reader)`，reader 从原 run 加载并复核当前身份，不能用队列正文重建完整身份。
3. 普通仓储始终显式传 Scope。控制面仅使用 ControlScope + ControlRepository 的登记用途精确查询，04/05 在其所属模块增加受限写服务。跨渠道统计另行授权并逐渠道执行。
4. 调用者合并全流程锁键后开启 `transaction`，传同一 UoW 给预算、快照及任务写服务；不嵌套工作单元。参见 [组合事务示例](examples/admission.py)。各服务所有锁键必须在开始事务前已知；外部网络调用在提交后。
5. 版本业务校验器负责资源存在、当前授权状态、能力匹配、依赖闭包及评测门禁；公共层只提供固定语义与存储机制，缺少校验器不能冻结或切换发布。
6. 新空内容范围在渠道/环境开通，或首次核准新主体的登记流程中显式调用 `RecoveryService.initialize_fresh`。已有数据恢复先 `block`，方案 25/27 的独立账本服务回放标记，再 `complete`；缺少证明、恢复代次不匹配或标记集合不同均拒绝。
7. 所有内容入口声明 ContentRef，在与写入相同的短事务中调用 DeletionGuard；内容派生同时登记 source_links。配置版本与运行快照已自动登记来源；各业务模块从首次落库起接入。新增实现的含内容表声明 cleanup_type，通过 build_core_services 的 configure_cleanup 登记处理器；归档状态须同步改为已实现，装配及模型检查缺项失败。

下载使用 `/api/v1/artifacts/{id}/content` 或管理用途的 `/admin/v1/artifacts/{id}/content`，返回 attachment、no-store、nosniff。服务端在访问对象前及交付字节前重查授权和删除标记，不签发对外对象链接。当前采用最多 20 MiB 的有界文件读写；大文件流式服务不属于 03，后续须按块复核身份与标记。

上传在短事务中登记 STAGED 和来源，提交后写私有对象，再在新事务中验证并标为 AVAILABLE。未登记成功的对象可按原元数据路径回收；清理保留无原文元数据并重复删除过期暂存对象，以覆盖迟到的失败上传。方案 25 负责周期调度与完整删除图。

出站校验返回 ValidatedTarget：包含原域名、端口与本次核准 IP 集合。07/10/14 传输适配器必须连接这些 IP 并保留原域名的 TLS/SNI 校验，禁止校验后再次不受控解析 DNS；重定向每一跳重新登记校验，不继承认证头。03 没有提供可由模型传入任意 URL 的通用 HTTP 执行器。

凭据只保存 nonce + AES-GCM 密文和 key_version，AAD 绑定渠道、环境、用途及凭据 id。外部密钥服务负责轮换和旧版本解密可用性；`CredentialService.call` 在服务端回调边界临时解密、复核授权与当前状态。不将明文返回业务 API，不声称 Python 可以保证内存擦除。模型价格仍只有 08 的 price_versions 一份账本。

## 检查入口

```bash
make check
make integration
make migrate
make storage-audit
make contracts
make model-check
make dependency-audit
```

前端执行 `pnpm api:generate && pnpm check && pnpm test:e2e`；依赖扫描为 `pnpm audit`。本地工具链可按根目录 README 增加 `.tools` 到 PATH。CI 在基础检查中运行模型/契约/源码审查，在真实 PostgreSQL 测试后核对实际 schema；依赖扫描单独作业，失败保留报告。

后续边界：04/05 提供真实认证与渠道，08 提供预算计价，11/17 提供任务和 SSE，25 提供全部模块的清理调度与独立删除账本，27 完成恢复演练。98 张表的设计基线不表示这些业务服务已经实现。04 已交付登录、管理接口与实时认证授权，并接入公共下载；业务 Key、真实渠道状态与模型调用继续由后续单元完成。见 [IAM 交接](iam.md)。
