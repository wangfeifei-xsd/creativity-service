# 配置指南

在管理端选择目标渠道与环境，按“模型 → 提示词、工具与 Skills → Agent → 评测与上线”完成配置。字段结构以 [OpenAPI](../contracts/openapi.json) 和 [配置示例](../examples/agents/README.md) 为准。

<a id="models"></a>

## 模型

创建供应商连接并保存凭据，登记模型和能力，执行连接及能力测试，再配置路由。路由可设置候选顺序、超时、重试和能力要求；流式文本要求模型声明并通过 streaming 能力验证。模型价格由用量模块统一维护，见 [预算与费用](operations.md#usage)。

保存未发布的路由版本仅要求模型管理权限。能力验证使用服务端固定用例进入统一运行时，记录调用与用量。发布由具有当前环境发布权限的人员完成，并检查模型能力和配置有效性；不要求发布者同时具备模型执行权限。业务智能体、工具调试及实际数据访问继续校验当前渠道环境的动作、资源及主体权限。

生成类能力验证优先使用模型默认参数中的 `max_tokens`，未配置时显式设置为 1024 Token，预算预占与实际发送采用同一上限；向量生成不设置生成输出上限。

API 和 Worker 的连接凭据与加密密钥应一致；HTTP 与 HTTPS 均无需配置出站许可，历史目的地配置仅保留读取兼容，见 [出站地址策略](../../技术方案.md#outbound)。完整可选环境变量见 [Settings](../src/creativity_service/core/config.py)，模型字段见 [模型契约](../contracts/internal/models.json)；供应商的实际兼容结果需单独验收。

视觉理解可在模型详情的“验证”中选择，默认仍为文本生成和用量口径。验证经统一运行时发送随机图形图片，识别颜色、形状和顺序后记录当前配置的能力证据；不要求原生结构化输出，单独验证不会覆盖其他能力。Chat Completions 与 Anthropic Messages 接受用户消息中的文本及 `image_url` 内容块，图片使用 PNG、JPEG 或静态 WebP 的 Base64 data URL；每张最多 5 MiB、1600 万像素，每次最多 10 张且合计不超过 20 MiB。适配器不下载外部图片地址或读取本地文件，业务图片调用同样检查视觉能力证据。协议夹具通过不代表具体供应商或模型已通过真实视觉验证。

<a id="prompts"></a>

## 提示词

编写模板并声明变量结构及取值来源，保存后即可发布，再由智能体选择使用。详情提供编辑、变量和渲染预览；预览不调用模型，也不是发布前置步骤。发布检查配置、资源状态、修订和权限，不要求模型路由、调试记录或评测记录。已发布提示词可直接修改，新受理任务使用新内容，已受理任务保留原快照。实际效果按需在智能体中调试或在效果评测中比较。配置结构见 [提示词契约](../contracts/internal/prompts.json)。

智能协助提取的提示词在确认保存方案时自动发布到当前环境，并关联指定模型步骤；每个步骤只关联一份提示词，变量按步骤输入映射校验。创建、发布和智能体配置保存共用事务，权限变化或保存失败全部回滚，重复保存返回已有结果。手动创建提示词仍默认未发布。

<a id="skills"></a>

## Skills

Web 先通过通用文件接口上传技能包，再以返回的 URL 请求预览和导入；技能配置保存该 URL。检查文件清单、内容及工具依赖，完成测试后发布。Agent 绑定技能资源，运行固定包快照并记录实际加载的文件和摘要。包格式与样例见 [Skills 示例](../examples/skills/README.md)，文件保留规则见 [OSS 说明](../deploy/oss/README.md)；可选脚本执行需额外配置受控执行环境。

技能列表的“智能创建”及列表、详情的“智能修改”进入 `/skills/assistance`，修改地址以 `skill` 参数指定目标。通过自然语言多轮生成完整技能书，可查看 Markdown、SKILL.md 原文、技能配置及修改对照。`POST /admin/v1/skills/assistance` 受理生成，`GET /admin/v1/skills/assistance/runs/{run_id}` 读取结果，`POST .../{run_id}/apply` 保存服务端候选。新建保持未发布；修改保留参考文件和兼容元数据，已发布技能沿用当前共享修改与权限规则。保存具备修订检查、原子写入及幂等保护；生成、修复与重试通过统一运行记录用量。

<a id="agents"></a>

## Agent

每个智能体只有一份当前配置，直接编辑和保存；智能修改确认后覆盖同一配置。详情和接口不提供版本、草稿、发布、回滚或发布记录。

新建默认下线且启用。上线／下线控制是否接受新的正式运行，启用／停用控制是否允许执行，两项状态独立。下线配置可调试，停用阻断新执行及已受理任务的下一外部调用。上线检查当前配置、依赖、能力、预算与权限；在线修改须覆盖全部上线环境的操作权限。

使用 `PATCH /admin/v1/agents/{id}/configuration` 保存配置，携带内部并发修订。`POST /admin/v1/agents/{id}/state` 接收 online、offline、enable、disable 和原因。校验、调试使用同一智能体地址下的 validate、tests。旧 versions、agent-versions、releases 接口已移除。

业务后端使用 agent_code 调用。受理固定配置与依赖内容，保存修改只影响新受理任务；历史快照继续保留，不能选择为配置或回滚。删除要求全部环境下线或停用，且没有关联计划、待派发任务或未结束运行，历史结果与用量保留。

## 配置交付

可复用样例位于 [Agent](../examples/agents/README.md)、[Skills](../examples/skills/README.md)、[MCP](../examples/mcp/README.md) 与 [接入演示](../examples/onboarding/README.md)。导入后绑定目标渠道实际可用的模型、工具和资料，再调试上线。

工具依赖按声明的能力与 schema 绑定；环境间迁移配置时重新确认本地资源和授权。源系统数据、规则和计算通过 [MCP 接入](integration.md#mcp-business) 提供。

<a id="enhancements"></a>

## 可选能力

| 能力 | 配置入口 |
| --- | --- |
| 向量检索与三层记忆 | [向量环境](../deploy/vector.md)、[记忆策略](runtime.md#memory) |
| 暂停恢复与业务写工具 | Agent 等待节点、工具写入策略和状态核查工具；审批需 `run:approve` 权限 |
| 节点 Python、Skills 脚本与 MCP stdio | `CREATIVITY_SANDBOX_PROFILES`；准备固定摘要镜像，参考 [构建文件](../deploy/Dockerfile.sandbox) |
| MCP OAuth、外部身份与角色 | 连接或身份配置，同时核对回调；MCP 凭据直接存储 |
| 调度、批量、Webhook、告警、供应商账单 | 对应管理模块；Worker 与 Beat 配套运行 |
| 多语言客户端 | [SDK](../sdks/README.md) |

完整配置字段及校验见 [Settings](../src/creativity_service/core/config.py)，请求结构见 [OpenAPI](../contracts/openapi.json)。第三方连接需要在目标环境实际验证。
