# 可导入的 Agent 初始配置

`matching.json`、`risk.json`、`analysis.json` 分别是匹配、风险、分析的可选初始正文，均使用平台通用 `workflow.v1`，不会安装业务处理器或表。业务调用编码和名称可以自行修改。

1. 在目标渠道与环境配置 MCP 连接、工具、提示词、模型和 Skills。
2. 修改 JSON 中的 `bindings`、输入输出 schema、步骤与参数；领域校验、计算和字段语义由业务 MCP / Skill 提供。
3. 使用管理 Token 将正文提交到 `POST /admin/v1/agents`。缺少依赖可以保存草稿，校验和发布仍会阻断。
4. 调试、评测并发布后，业务后端通过统一运行 API 调用配置的 `agent_code`。

这三份正文只是配置起点，不附带真实领域逻辑或供应商验收结果。自定义用途复用相同通用入口，无需在平台代码登记业务名称。旧配置迁移见 [19 交接](../../docs/access-decoupling.md)。

## 21 的两套完整配置

`text-brief.json` 是纯文本结构化任务；`archive-answer.json` 是 MCP 查询、模型解读与对象组装的三步流程。对应 Skills ZIP 在 `../skills/packages/`，提示词、依赖清单、输入样本分别在 `prompts/`、`manifests/`、`cases/`。样本中的 `fixture_output` 明确是受控模型替身输出，不代表真实模型效果。

先导入并冻结技能，在本渠道选择真实资源版本，再运行 `prepare.py 模板路径 绑定JSON路径 输出路径`。绑定 JSON 的键取自相应依赖清单的 `resources`，值是目标渠道实际版本 ID；生成的请求正文提交到现有 Agent 创建 API。工具名称可以映射到不同本地编码，但版本名称、来源和声明契约须满足要求。缺少资源不能靠准备脚本自动选择。

完整导入、调试、评测及环境发布顺序见 [21 配置交接](../../docs/configuration-delivery.md)。MCP 样例使用已有 [档案测试服务](../mcp/README.md)；正式业务事实和权限由接入方提供。
