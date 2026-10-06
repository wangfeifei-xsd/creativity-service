# 配置指南

在管理端选择目标渠道与环境，按“模型 → 提示词、工具与 Skills → Agent → 评测与发布”完成配置。字段结构以 [OpenAPI](../contracts/openapi.json) 和 [配置示例](../examples/agents/README.md) 为准。

<a id="models"></a>

## 模型

创建供应商连接并保存凭据，登记模型和能力，执行连接及能力测试，再配置路由。路由可设置候选顺序、超时、重试和能力要求；流式文本要求模型声明并通过 streaming 能力验证。模型价格由用量模块统一维护，见 [预算与费用](operations.md#usage)。

保存未发布的路由版本仅要求模型管理权限。能力验证使用服务端固定用例进入统一运行时，记录调用与用量。发布由具有当前环境发布权限的人员完成，并检查模型能力和配置有效性；不要求发布者同时具备模型执行权限。业务智能体、工具调试及实际数据访问继续校验当前渠道环境的动作、资源及主体权限。

API 和 Worker 的连接凭据、加密密钥与出站许可应一致。完整可选环境变量见 [Settings](../src/creativity_service/core/config.py)，模型字段见 [模型契约](../contracts/models/)；供应商的实际兼容结果需单独验收。

<a id="prompts"></a>

## 提示词

声明变量结构及取值来源，保存草稿、渲染样例并调试，再冻结和发布版本。运行使用冻结版本及声明变量；修改草稿不会改变已受理任务。配置结构见 [提示词契约](../contracts/prompts/)。

<a id="skills"></a>

## Skills

上传技能包，检查 manifest、文件及工具依赖，完成测试后发布。Agent 固定绑定技能版本，运行记录实际加载的文件和摘要。包格式与样例见 [Skills 示例](../examples/skills/README.md)；可选脚本执行需额外配置受控执行环境。

<a id="agents"></a>

## Agent

选择通用执行入口 `structured.v1`、`workflow.v1`、`tool_loop.v1` 或 `stateful.v1`，配置输入输出 schema、模型路由、提示词、工具和 Skills，并设置会话、记忆和调用限额。校验与调试使用固定候选；发布环境映射只引用已验证的具体版本。

业务后端使用 `agent_code` 调用。结果内容符合 Agent 的输出 schema，领域字段由配置定义。正式发布需要 [评测门禁](operations.md#evaluations)；变更内容或依赖后重新评测。

<a id="configuration-delivery"></a>

## 配置交付

可复用样例位于 [Agent](../examples/agents/README.md)、[Skills](../examples/skills/README.md)、[MCP](../examples/mcp/README.md) 与 [接入演示](../examples/onboarding/README.md)。导入后绑定目标渠道实际可用的模型、工具和资料，再调试发布。

工具依赖按声明的能力与 schema 绑定；环境间迁移配置时重新确认本地资源和授权。源系统数据、规则和计算通过 [MCP 接入](integration.md#mcp-business) 提供。

<a id="enhancements"></a>

## 可选能力

| 能力 | 配置入口 |
| --- | --- |
| 向量检索与三层记忆 | [向量环境](../deploy/vector.md)、[记忆策略](runtime.md#memory) |
| 暂停恢复与业务写工具 | Agent 等待节点、工具写入策略和状态核查工具；审批需 `run:approve` 权限 |
| Skills 脚本与 MCP stdio | `CREATIVITY_SANDBOX_PROFILES`；准备固定摘要镜像，参考 [构建文件](../deploy/Dockerfile.sandbox) |
| MCP OAuth、外部身份与角色 | 连接或身份配置，同时核对加密密钥、回调和出站许可 |
| 调度、批量、Webhook、告警、供应商账单 | 对应管理模块；Worker 与 Beat 配套运行 |
| 多语言客户端 | [SDK](../sdks/README.md) |

完整配置字段及校验见 [Settings](../src/creativity_service/core/config.py)，请求结构见 [OpenAPI](../contracts/openapi.json)。第三方连接需要在目标环境实际验证。
