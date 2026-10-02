# 09 提示词管理交接

执行方案：[09](../../代码编写执行方案/09-提示词管理.md)；需求：PRM-F01—F11。规则引用 [rule.md](../../rule.md)，验证见 [prompts-validation.md](prompts-validation.md)。

## 已交付

- 后端 `modules/prompts/`：资源、不可变发布版本、revision 草稿、变量、样例、预览、冻结调试描述、版本差异、引用、发布/回滚、退役、受控导入导出。
- 前端 `src/features/prompts/`：列表及编辑、变量、预览、调试、版本、引用六个操作区。独立 `registration.ts` 由工作区注册表自动发现，动作取自服务端；具备版本读取权限的审计人员也可看到提示词入口。
- `0009_prompts` 创建 `prompts`、`prompt_samples`、`prompt_tests`。合并本批 `0004_models`、`0004_usage` 分支；`0010_tools` 接在其后。未更改其他模块的父修订。
- 版本、引用、环境映射复用公共表；样例和调试记录严格限定当前渠道、环境、数据域与主体。版本正文中的变量和分区继承所属版本的渠道，不另存可被覆盖的渠道字段。

## 管理接口

所有接口位于 `/admin/v1`，管理 Token 选择的工作区确定执行范围；正文不接受渠道、环境或平台身份变量。

| 路由 | 用途 |
| --- | --- |
| `GET/POST /prompts`、`GET/PATCH /prompts/{id}` | 列表、创建及资料编辑 |
| `GET/POST /prompts/{id}/versions`、`GET/PATCH /prompt-versions/{id}` | 版本列表、建草稿、读取、revision 编辑 |
| `POST /prompt-versions/{id}/render` | 预览，不调用模型；默认脱敏 |
| `GET/POST /prompts/{id}/samples` | 当前范围的固定样例；原文需独立敏感数据权限 |
| `GET /prompt-model-routes` | 已发布且获授权的具体模型路由版本 |
| `POST /prompt-versions/{id}/test-descriptors` | 保存冻结描述；不代表已执行或测试通过 |
| `GET/POST /prompt-versions/{id}/tests` | 调试历史 / 进入统一运行 |
| `GET /prompt-tests/{id}`、`POST /prompt-tests/{id}/submit` | 查看旧快照 / 幂等提交已固定描述 |
| `GET /prompt-versions/{id}/compare/{previous_id}` | 变量、来源、长度和输出要求差异及引用影响 |
| `GET/POST /prompts/{id}/releases` | 当前环境映射 / 发布及回滚 |
| `GET /prompts/{id}/references`、`POST /prompt-versions/{id}/retire` | 固定依赖影响 / 受引用保护的退役 |
| `POST /prompts/import`、`POST /prompt-versions/{id}/exports` | 文本或结构化定义交换；导出返回受控 Artifact |

发布的 `revision` 对应版本修订；`expected_mapping_revision` 对应当前环境映射，首次为 null。`operation=rollback` 只能选择已有发布版本。映射切换不会更新 Agent 版本或历史运行快照。已发布版本不再编辑，退役先检查所有环境的映射和已发布资源引用。

## 渲染与导出

有限语法为 `{{ name }}`、`{{ name|json }}`、字符串的 `upper/lower`；不支持表达式、属性访问、循环或代码执行。替换值不再解析为模板。系统指令和输出要求只允许平台变量；其余消息的变量来源须与分区一致。平台白名单为 `channel_id/environment/principal_id/actor_id/data_scope_id/subject_id`，全部来自 AuthContext。

变量明确类型、中文显示名、来源、敏感级别和字符长度上限。对象及数组按规范 JSON 字符数检查长度；布尔值不作为整数。未声明、缺失、超长或来源错误返回 `PROMPT_VARIABLE_INVALID`、字段路径及中文名称。

渲染前先检查展开规模，最多一百万字符，避免重复大变量造成无界展开。预览显示分区、脱敏状态、截断字符数和估算 Token。截断只影响展示，调试描述保存完整内容。预览未选择模型路由，因此上下文上限和剩余额度为 null；估算不是供应商 Token 统计。固定样例、工具和记忆预览默认整体掩码，不能通过将变量标成“公开”泄漏既有敏感内容。

结构化格式为 `PromptPortable`（`format_version=1`）。文本导出以“提示词文本 v1”开头，包含变量元数据及中文分区；每段显式字符长度，正文不能伪造分区边界。普通无头文本可导入为系统指令。导出删除敏感变量默认值，不携带样例、模型结果、供应商凭据、授权或渠道绑定。下载复用 `/artifacts/{id}/content` 的身份、权限、删除标记和内容校验。

## 16/17 的装配接口

- `PromptService.read_version(context, version_id)` 读取具体版本；`dependency_summary` 只接受已发布版本，返回内容/依赖摘要、变量定义及输出要求。
- `PromptDebugService.prepare` / `read_descriptor` 交付 `PromptDebugDescriptor`：固定 Scope、提示词版本、草稿 revision、具体模型路由、样例摘要、来源分区、输出约束和描述摘要。管理 API 只返回脱敏视图，内部运行端口取得完整描述。
- `PromptService.runtime_render(context, frozen_version, bindings)` 校验渠道、摘要和删除标记；调用方使用已冻结的版本，不按环境映射重新解析。
- `PromptContextProvider.resolve` 返回受信工具/记忆绑定及 ContentRef 来源。来源会写入调试派生图，删除源内容后阻断读取及发布；不得改写调用输入。
- `PromptDebugRunner.submit` 由 17 注入：按渠道及 test_id 幂等受理，purpose 固定为 debug；同一受理事务包含预算、运行、任务和执行快照，并重新核对当前授权与依赖。外部调用不在提示词数据库事务内等待。返回统一 run_id、release_snapshot_id、rendered_input_ref；回写重试必须得到相同运行。
- `PromptEvidenceReader.read` 由 17 注入：只在传入 UoW 内查询真实运行和用量记录，不调用网络。证据须精确匹配 Scope、run_id、模型路由和 descriptor_digest，并同时满足执行成功、约束通过、用量已入账。客户端没有上传“通过”证据的接口。24 的批量评测由其自身接入同一冻结描述，不在本模块模拟。

生产装配没有模拟 runner、evidence 或工具/记忆数据。缺少统一运行时，提交测试返回 503，已固定描述保留以便后续执行；缺少真实测试证据时冻结及发布都拒绝。历史结果通过受信证据端口读取，原文需要敏感数据及运行内容权限。

`register_prompt_cleanup` 登记样例和调试清理器；新记录从首次写入就保存版本、样例、上下文和运行来源。25 接入批量调度、保留与清理，27 承担正式部署迁移与恢复验收。
