# 开发指南

开发前阅读 [项目规则](../../rule.md) 和对应的 [模块需求](../../需求文档/00-需求总纲.md)。启动命令见 [本地开发](local-development.md)，检查命令见 [测试指南](testing.md)。

<a id="bootstrap"></a>

## 工程与配置

Python 3.12、uv 0.10.12；基础设施为 PostgreSQL、Redis 和 S3 兼容对象存储。精确依赖以 [pyproject.toml](../pyproject.toml) 与 [uv.lock](../uv.lock) 为准。

配置从 `.env` 加载，进程环境优先；变量示例见 [.env.example](../.env.example)，完整字段及校验见 [Settings](../src/creativity_service/core/config.py)。API、Worker 和 Beat 使用相同数据库、凭据主密钥、出站策略及资源配置。缓存、认证、队列和任务结果使用独立 Redis 数据库。

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

受理事务示例见 [admission.py](../examples/development/admission.py)。受理时固定版本和依赖；调用外部模型或工具的步骤交给统一运行服务，具体端口见 [运行指南](runtime.md#runs)。

模型变更同步维护 [catalog.json](data-model/catalog.json)、Alembic 修订与 [初始化 SQL](../sql/README.md)。字段文档通过 `uv run python scripts/render_data_model.py` 生成。接口变更执行 `make contracts`，然后按 [前端说明](../../creativity-web/README.md#接口类型生成) 更新生成类型。

<a id="iam"></a>

## 账号与权限

首次管理员通过 `uv run creativity-iam init-admin --login-name admin --display-name 管理员` 创建。管理端登录后使用服务端保存的 Token；操作权限、资源授权及工作区由 IAM 服务恢复。账号停用、角色或成员撤销会影响后续访问；认证 Redis 不可用时返回 503。

账号密码登录须先完成滑块拼图：`POST /admin/v1/auth/captcha/challenges` 提交 `login_name` 获取 PNG 底图、拼块和挑战标识；`POST /admin/v1/auth/captcha/verify` 提交 `challenge_id` 与原图坐标中的横向 `offset`，成功后将返回的 `captcha_token` 随登录请求提交。前端复用 antd 表单、弹窗与滑块，支持鼠标、触屏和方向键调整、回车确认；验证码失败不清空账号密码。

挑战有效期 120 秒，允许横向误差 4 像素；验证凭据有效期 60 秒，绑定规范化登录名与服务端获取的客户端 IP。Redis 键与记录归系统渠道，挑战及凭据通过原子消费防止并发重放，无 TTL 的记录拒绝使用。每次挑战只允许验证一次，账号密码登录尝试会消费验证凭据，错误密码也须重新验证。挑战签发限每 IP 每分钟 30 次，验证限每 IP 每分钟 60 次，并保留原账号/IP 登录限速。该模块提供基础自动化请求拦截；缺口仅包含在位图中，浏览器不接收正确偏移量。外部身份交换继续由配置的身份源校验，初始密码修改属于已认证流程。

`uv run creativity-iam reconcile-revocations` 补偿未完成的撤销。业务 Key 换 Token 和主体委托见 [接入指南](integration.md#integrations)。接口详情查 [完整 OpenAPI](../contracts/openapi.json)。

<a id="channels"></a>

## 渠道

通过 `make channels-init` 初始化系统渠道，在管理端创建业务渠道、环境、数据域、接入服务和 Key。外部数据域使用显式映射；服务 Key 与主体委托密钥分别管理。渠道暂停、归档、恢复和 Key 轮换由渠道服务处理，在途运行与预算状态参与检查。

平台并发与渠道限额分别配置，运行受理同时核对。字段和状态定义见 [渠道模型](data-model/modules/channels.md)，调用身份见 [接入指南](integration.md)。

<a id="workspace"></a>

## 管理页面

前端从服务端会话读取工作区、导航和允许动作；页面通过 feature registration 接入。接口类型从 OpenAPI 生成，字段错误、运行详情和删除进度复用公共组件。具体入口见 [前端开发说明](../../creativity-web/README.md) 和 [页面接入](../../creativity-web/docs/workspace.md)。
