# 可导入的 Agent 初始配置

`matching.json`、`risk.json`、`analysis.json` 分别是匹配、风险、分析的可选初始正文，均使用平台通用 `workflow.v1`，不会安装业务处理器或表。业务调用编码和名称可以自行修改。

1. 在目标渠道与环境配置 MCP 连接、工具、提示词、模型和 Skills。
2. 修改 JSON 中的 `bindings`、输入输出 schema、步骤与参数；领域校验、计算和字段语义由业务 MCP / Skill 提供。
3. 使用管理 Token 将正文提交到 `POST /admin/v1/agents`。缺少依赖可以保存草稿，校验和发布仍会阻断。
4. 调试、评测并发布后，业务后端通过统一运行 API 调用配置的 `agent_code`。

这三份正文只是配置起点，不附带真实领域逻辑或供应商验收结果。自定义用途复用相同通用入口，无需在平台代码登记业务名称。旧配置迁移见 [19 交接](../../docs/access-decoupling.md)。
