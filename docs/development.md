# 开发指南

开发前阅读 [项目规则](../../rule.md) 和对应的 [模块需求](../../需求文档/00-需求总纲.md)。启动命令见 [本地开发](local-development.md)，检查命令见 [测试指南](testing.md)。

<a id="bootstrap"></a>

## 工程与配置

Python 3.12、uv 0.10.12；基础设施为 MySQL 8、Qdrant、Redis 和 阿里云 OSS。精确依赖以 [pyproject.toml](../pyproject.toml) 与 [uv.lock](../uv.lock) 为准。

通过启动进程的 `CREATIVITY_ENVIRONMENT` 选择配置，未设置时使用 `dev`；兼容旧值 `development`、`production`，分别归一为 `dev`、`prod`。选择变量须在启动命令或部署环境中设置，不能仅靠修改配置文件内的值切换环境。API、Worker、Beat、迁移及各模块共用此规则，均从当前服务工作目录读取文件。

| 环境 | 配置文件 |
| --- | --- |
| 本地 `dev` | [.env.dev](../.env.dev) |
| 测试 `test` | [.env.test](../.env.test) |
| 生产 `prod` | [.env.prod](../.env.prod) |

每个环境只读取对应的一份配置文件。覆盖优先级从高到低为：显式构造参数、进程环境变量、当前环境配置文件、代码默认值。本地开发配置放在 `.env.dev`，测试配置放在 `.env.test`；两份文件可能包含连接凭据，均保持 Git 忽略。不再使用 `.env`、`.env.local` 或 `.env.<环境>.local`。

dev 和 test 共用已配置的 Qdrant 实例，通过独立集合前缀隔离，连接说明见 [向量环境](../deploy/vector.md)。测试环境的其他连接配置及生产环境的连接地址、账号与密钥需在启动前填写或通过部署环境变量注入；缺少必要配置会启动失败。MySQL 使用各环境独立的数据库，缓存、认证、队列和任务结果使用独立 Redis 数据库。Redis 前缀、OSS 的服务与部署环境前缀、Qdrant 集合前缀及删除清单路径按环境区分；OSS 可共用桶，配置与 RAM 权限见 [OSS 配置](../deploy/oss/README.md)。删除清单路径必须挂载该环境 API/Worker 共用的独立持久卷，详细要求见 [数据生命周期](operations.md#data-lifecycle)。浏览器来源在 `CREATIVITY_CORS_ORIGINS` 中按 JSON 数组配置。

本地启动使用 `./scripts/start-local.sh`。测试或生产准备好连接配置后，在各进程的终端或部署定义中设置相同环境；以下以测试环境为例，生产将 `test` 改为 `prod`：

```bash
export CREATIVITY_ENVIRONMENT=test
uv run alembic upgrade head
uv run uvicorn creativity_service.app:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log
# 在继承同一环境配置的其他终端或进程中启动：
uv run celery -A creativity_service.workers.app:app worker --loglevel=INFO
uv run celery -A creativity_service.workers.app:app beat --loglevel=INFO
```

完整字段及校验见 [Settings](../src/creativity_service/core/config.py)。API、Worker 和 Beat 使用相同数据库、凭据主密钥、出站策略及资源配置。

| 代码入口 | 职责 |
| --- | --- |
| [app.py](../src/creativity_service/app.py) | 应用工厂、生命周期和服务装配 |
| [core](../src/creativity_service/core/) | 配置、上下文、认证、事务、版本、文件与契约 |
| [modules](../src/creativity_service/modules/) | 各模块服务、接口、仓储和数据结构 |
| [integrations](../src/creativity_service/integrations/) | 模型、工具及外部系统适配 |
| [workers](../src/creativity_service/workers/) | 执行、调度、恢复与清理 |

<a id="core"></a>

## 模块接入

从服务端认证得到 `AuthContext/Scope`，在模块服务中组织业务，再使用公共事务、锁、版本与文件能力。模块通过装配函数登记路由、资源读取器和清理处理器；公共层契约见 [contracts](../contracts/README.md)。

受理事务实现见 [AdmissionService](../src/creativity_service/modules/runs/admission.py)。受理时固定版本和依赖；调用外部模型或工具的步骤交给统一运行服务，具体端口见 [运行指南](runtime.md#runs)。

模型变更同步维护 [catalog.json](data-model/catalog.json)、Alembic 修订与 [初始化 SQL](../sql/README.md)。字段文档通过 `uv run python scripts/render_data_model.py` 生成。接口变更执行 `make contracts`，然后按 [前端说明](../../creativity-web/README.md#接口类型生成) 更新生成类型。

MySQL 逻辑删除的实现入口是 [soft_delete.py](../src/creativity_service/core/database/soft_delete.py)。范围仓储默认筛选有效记录；直接 SQLAlchemy 查询须在构造完整查询后调用 `active_rows()`，保证计数、分页和外连接使用同一条件。`Repository.remove()` 执行逻辑删除，内容清理使用明确的物理删除入口。需要处理删除记录的后台任务和历史查询显式传入 `include_deleted=True`，不改变原有渠道与授权边界。规范统一见项目规则。

<a id="iam"></a>

## 账号与权限

首次管理员通过 `uv run creativity-iam init-admin --login-name admin --display-name 管理员` 创建。管理端登录后使用服务端保存的 Token；操作权限、资源授权及工作区由 IAM 服务恢复。账号停用、角色或成员撤销会影响后续访问；认证 Redis 不可用时返回 503。

用户菜单提供“修改密码”，平台与渠道会话均调用 `POST /admin/v1/auth/change-password`。普通改密必须提交正确的 `current_password`；首次登录及重置后处于强制改密状态时，只提交 `new_password`，页面另行确认两次新密码一致。免验原密码由服务端当前账号状态决定；新密码为 12 至 256 个字符且不可与当前密码相同。凭据写入在互斥事务中复核会话、凭据版本及账号修订，成功后撤销该账号全部管理会话，前端清理 Token 并返回登录页。

管理账号从角色目录多选内置或自定义角色；`GET /admin/v1/accounts/roles` 返回当前可授予的角色。创建和修改账号提交 `roles`、`channel_ids`，同一账号可同时具有平台与渠道角色；所选渠道角色同步到明确授权的多个渠道，只有平台角色时不提交渠道。IAM 在显式目标渠道的独立工作单元中共享一个数据库事务，统一取锁，原子更新账号、渠道成员、普通资源授权及审计；无效渠道或 revision 冲突全部回滚。列表批量装配当前页账号的渠道名称；整组授权改变时撤销原会话。既有 `role`、`platform_roles` 和细粒度授权接口保持兼容。

平台管理员登录后直接显示平台导航；渠道管理员根据会话的 `default_workspace` 自动取得渠道 Token。`workspace_options` 返回当前有效范围，`can_access_platform` 控制平台入口展示，返回平台接口仍独立鉴权。同时拥有两种作用域角色且存在有效渠道范围时，可在顶部往返切换管理模式；分步开通但尚未配置环境的渠道不会形成可进入范围，渠道模式保持禁用并提示配置环境及成员授权。默认渠道及范围稳定排序，不把缺失授权回退到系统渠道。浏览器顶部直接切换渠道、环境；旧 Token 及迟到响应不能进入新页面范围。

账号密码登录须先完成滑块拼图：`POST /admin/v1/auth/captcha/challenges` 提交 `login_name` 获取 PNG 底图、拼块和挑战标识；`POST /admin/v1/auth/captcha/verify` 提交 `challenge_id` 与原图坐标中的横向 `offset`，成功后将返回的 `captcha_token` 随登录请求提交。前端复用 antd 表单、弹窗与滑块，支持鼠标、触屏和方向键调整、回车确认；验证码失败不清空账号密码。

每次打开验证、点击“换一张”或验证失败后生成新挑战，服务端从系统渠道内置的 10 张风景照片随机选图，并随机确定缺口位置。底图随 Python 包分发，运行时无外部图片请求；解码后的缓存仅包含这组公共素材，每个挑战在独立副本上生成拼图。照片已裁切为 2:1，覆盖草丘、雪峰湖泊、海岸、沙丘、瀑布、秋湖、花田、雪林、极光和雾凇。原图、作者与 CC0 许可见 [风景图库来源](../src/creativity_service/modules/iam/assets/captcha/system/README.md)。

挑战有效期 120 秒，允许横向误差 4 像素；验证凭据有效期 60 秒，绑定规范化登录名与服务端获取的客户端 IP。Redis 键与记录归系统渠道，挑战及凭据通过原子消费防止并发重放，无 TTL 的记录拒绝使用。每次挑战只允许验证一次，账号密码登录尝试会消费验证凭据，错误密码也须重新验证。挑战签发限每 IP 每分钟 30 次，验证限每 IP 每分钟 60 次，并保留原账号/IP 登录限速。该模块提供基础自动化请求拦截；缺口仅包含在位图中，浏览器不接收正确偏移量。外部身份交换继续由配置的身份源校验，初始密码修改属于已认证流程。

`uv run creativity-iam reconcile-revocations` 补偿未完成的撤销。渠道 Key 与密钥换 Token 及可选用户标识见 [接入指南](integration.md#integrations)。接口详情查 [完整 OpenAPI](../contracts/openapi.json)。

<a id="channels"></a>

## 渠道

通过 `make channels-init` 初始化系统渠道，在管理端创建业务渠道和环境，在“业务接入 → 渠道 Key 与密钥”创建凭据，内部接入身份由平台维护。渠道暂停、归档、恢复和 Key 轮换由渠道服务处理，在途运行与预算状态参与检查。

渠道列表接口 `/admin/v1/channels/page` 的 configuration_status 只返回环境配置状态和详情链接；批量聚合当前页渠道。接入服务和 Key 未配置不影响已授权用户进入渠道，实际调用仍校验环境、凭据与动作。

平台并发与渠道限额分别配置，运行受理同时核对。字段和状态定义见 [渠道模型](data-model/modules/channels.md)，调用身份见 [接入指南](integration.md)。

<a id="workspace"></a>

## 管理页面

前端从服务端会话读取工作区、导航和允许动作；页面通过 feature registration 接入。接口类型从 OpenAPI 生成，字段错误、运行详情和删除进度复用公共组件。具体入口见 [前端开发说明](../../creativity-web/README.md) 和 [页面接入](../../creativity-web/docs/workspace.md)。
