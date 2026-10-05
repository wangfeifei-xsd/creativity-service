# 技能包示例

| 技能 | 用途 | 导入包 |
| --- | --- | --- |
| [text-brief](text-brief/SKILL.md) | 文本摘要与输出约定，始终加载 | [text-brief.zip](packages/text-brief.zip) |
| [archive-answer](archive-answer/SKILL.md) | MCP 查询与引用规则，按需加载 | [archive-answer.zip](packages/archive-answer.zip) |

每套包含技能正文、参考资料和 `.platform/skill.json`；测试核对源码与 ZIP 字节一致。在技能管理页上传 ZIP，填写本地编码与负责人，并显式绑定本渠道的工具版本。导入生成独立草稿，缺少依赖时不能冻结、发布或加载。

Agent、提示词、依赖清单和输入样本见 [Agent 配置](../agents/README.md)，完整操作见 [配置交付](../../docs/configuration.md#configuration-delivery)。样例用于验证配置流程，业务政策和真实数据由接入方提供。
