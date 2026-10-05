# 文档

启动项目先看 [项目 README](../README.md)；其余内容按需查阅。

| 要做什么 | 文档 |
| --- | --- |
| 启动服务、查看日志 | [本地开发](local-development.md) |
| 修改代码、接入模块、处理账号与渠道 | [开发指南](development.md) |
| 配置模型、提示词、Skills 和 Agent | [配置指南](configuration.md) |
| 业务后端调用 API、接入 MCP 工具 | [接入指南](integration.md) |
| 排查任务、SSE、会话与记忆 | [运行指南](runtime.md) |
| 管理费用、评测、删除与恢复 | [运维指南](operations.md) |
| 执行检查与验收 | [测试指南](testing.md) |

字段及存储关系查 [数据模型](data-model/README.md)，接口查 [OpenAPI](../contracts/openapi.json)，初始化数据库查 [SQL 说明](../sql/README.md)。

数据模型由脚本生成；验收结果按 [测试指南](testing.md) 在本地生成。

开发规范只在 [rule.md](../../rule.md) 维护，产品范围与设计分别见 [需求总纲](../../需求文档/00-需求总纲.md) 和 [技术方案](../../技术方案.md)。
