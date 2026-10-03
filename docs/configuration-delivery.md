# 21 Skills 与 Agent 配置交付

完成日期：2026-10-03。依据 [执行方案 21](../../代码编写执行方案/21-Skills与Agent配置交付.md)，开发规范引用 [rule.md](../../rule.md)。本单元复用 15/16/17/20 的包、版本、快照、发布和执行器，增量位于现有 Skills、Agent、运行模块与管理页面；没有新增业务场景服务、数据库表或迁移。

## 交付内容

| 样例 | 结构 | 可导入制品 |
| --- | --- | --- |
| 文本要点整理 | 文本输入 → 单次结构化模型输出；始终加载技能与输出约定 | [技能 ZIP](../examples/skills/packages/text-brief.zip)、[Agent](../examples/agents/text-brief.json)、[提示词](../examples/agents/prompts/text-brief.json)、[依赖清单](../examples/agents/manifests/text-brief.json)、[样本](../examples/agents/cases/text-brief.json) |
| 档案摘录解读 | MCP 查询 → 模型解读 → 通用对象组装；显式触发按需技能与引用资料 | [技能 ZIP](../examples/skills/packages/archive-answer.zip)、[Agent](../examples/agents/archive-answer.json)、[提示词](../examples/agents/prompts/archive-answer.json)、[依赖清单](../examples/agents/manifests/archive-answer.json)、[样本](../examples/agents/cases/archive-answer.json) |

两个样例的调用编码、输入字段、输出字段、步骤与资源选择均在配置文件中。修改或复制这些配置即可增加用途，不新增 Python 任务类、节点注册项或 API。档案查询复用 [20 的受控 MCP 服务](../examples/mcp/README.md) 中的 `archive.find-notes`；实际业务方提供自己的数据、权限、规则与领域计算。旧匹配、风险、分析样例继续是可选起点。

## 可移植工具依赖

`.platform/skill.json` 继续使用 `skill-package-v1`，在 `tool_requirements` 中声明可移植名称、版本名称、可选工具来源和输入输出 JSON Schema。平台字段 `tool_bindings` 保存“可移植名称 → 本渠道具体工具版本”的显式对应关系；源工具本地编码可以不同。导入请求或草稿编辑时选择目标版本，不按名称自动搜索或跨渠道引用。

```json
{
  "tool_bindings": {
    "archive.find-notes": "目标渠道已授权工具版本标识"
  }
}
```

名称对应声明项，版本名称必须满足要求；声明了来源或 schema 时必须一致。当前采取保守契约检查，不推断复杂 JSON Schema 的语义兼容；描述性 schema 改动也需重新确认声明。结构验证复用工具模块的本地引用规则，不获取外部 schema。

缺少绑定可保存为待补齐的草稿，校验明确列出缺项，冻结、发布和加载均拒绝。显式选择不存在、跨渠道、未授权、停用或契约不兼容的版本时，保存拒绝并保持原 revision。工具使用权限和 required_scopes 在事务外复核身份，并在绑定写入、冻结或发布事务内持有授权锁重新检查；Agent 绑定再次验证完整依赖闭包和工具白名单。Skill 指令不增加权限。

导出移除 `tool_bindings`、`allowed_agents`，不会携带源版本 ID 或授权。导入归档若自行携带非空本地绑定则拒绝。旧存储内容缺少新字段时，仅从原 `required_tool_versions` 恢复既有固定引用；不会重新按当前名称发现工具，也不会改写历史内容摘要。旧包导入仍要显式绑定；仅含版本声明的旧格式继续兼容，新交付包提供完整 schema。

## 操作顺序

1. 进入目标渠道和环境，配置已获授权的模型路由。导入 `prompts/` 对应正文作为提示词草稿，完成提示词调试后冻结。MCP 样例先按 20 配置连接、凭据、发现与工具发布，版本名称使用“配置初版”；模型路由须具备清单中的能力。业务调用还需服务 Key、主体委托和当前主体复核配置。
2. 在技能管理页选择“导入技能包”。上传后展示服务器校验的文件清单、指令和依赖契约，为每项依赖显式选择本渠道工具。保存后可查看文件、校验依赖和验证加载，再冻结技能版本。
3. 复制依赖清单中的占位符名称到本地绑定文件，将值填写为目标渠道的真实版本。文本样例需 `bind_prompt`、`bind_model_route`、`bind_skill`；MCP 样例还需 `bind_archive_tool`，与技能实际绑定版本保持一致。
4. 在服务端工程目录生成可提交的 Agent 请求正文：

   ```sh
   uv run python examples/agents/prepare.py \
     examples/agents/archive-answer.json /tmp/archive-bindings.json /tmp/archive-agent.json
   ```

   `prepare.py` 只做本地显式替换和静态检查，不选择资源、不连接服务、不发布。将生成 JSON 作为正文提交到 `POST /admin/v1/agents`；也可在智能体配置页填写相同设置。所有管理请求使用当前工作区管理 Token，不传入 channel_id 或模型可覆盖的身份字段。
5. 调用版本 `validate`，通过 `POST /admin/v1/agent-versions/{id}/tests` 提交 `revision`、样本 `input` 与幂等键。页面复用通用运行查看器，检查实际加载文件、工具结果、输出和用量。需要业务主体的 MCP 工具不会由普通管理身份绕过主体校验；可发布到测试环境后，经已签名的业务委托调用统一运行 API 取得主体范围内的执行证据。
6. 固定样本评测使用 `freeze_candidate(..., "evaluation")` 和统一运行入口，候选包含内容与完整依赖摘要。测试环境发布复用现有 release API；正式环境使用 [24 的样本与报告门禁](evaluations.md)。正式评测先固定已发布依赖，配置调试、受控模型替身和样例断言均不能替代业务正确性验收。

新增 `POST /admin/v1/skills/imports/preview` 仅验证上传内容并返回元数据、清单、指令和可移植设置，不持久化草稿。导入与 PATCH 技能版本使用同一个包解析器和工具依赖解析器。OpenAPI、前端生成类型和模块契约已同步。

## 资料加载与通用步骤

Agent 的 `bindings.skill_loading` 为已绑定技能配置加载方式、优先级、按需触发和具体参考文件。未覆盖项沿用技能设置；按需未选中时只提供名称描述。不存在或不可加载的资料在依赖检查中拒绝，实际加载、省略、文件 SHA-256 与触发原因保存在运行输入轨迹。编辑器使用文件选择器并标明脚本不可加载。输入变量来自 Agent 的原始合法输入，不因模型步骤映射改名而丢失。

`compute` 步骤配置 `operator: "object"`，将已有 `inputs` 字段映射与常量组装为输出对象；静态检查输入可赋值性，执行时再校验输出 schema。没有代码求值、业务算法或新任务注册。原受信内部步骤注册机制保留兼容。复杂计算仍应绑定业务 MCP。

固定工具步骤向后续模型提供完整 `tool_results`，包含本次返回的数据、source_version、observed_at、coverage 和 evidence_refs。步骤字段映射继续使用工具业务 data。工具结果的时间和数据版本来自实际调用或明确的缓存事实；配置快照的捕获时间不表示业务数据新鲜度。

## 快照、评测和后续交接

技能指令、参考资料和加载选择、工具与提示词具体版本、Agent 输入输出 schema 均进入已有版本或候选摘要。修改引用或资料会产生不同候选，必须重新验证；历史已发布版本与已受理 run 的快照保持原内容。生产发布门禁仍由受信评测报告匹配当前候选，不允许用客户端声明或本次调试记录绕过。

本单元没有正式发布真实业务 Agent。受控验证使用隔离 PostgreSQL schema、Redis 和本机 TCP MCP，模型输出明确为 fixture；隔离资源在测试后清理。[验证记录](configuration-validation.md) 和 [调试摘录](configuration-debug.json) 保留可重跑的证据。22 使用这些制品完成统一 API 客户端交付；23 补固定构建物复用证明；24/26 完成正式门禁、真实供应商与业务效果验收。
