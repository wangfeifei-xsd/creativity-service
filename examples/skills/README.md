# 技能包示例

| 技能 | 用途 |
| --- | --- |
| [text-brief](text-brief/SKILL.md) | 文本摘要与输出约定，始终加载 |
| [archive-answer](archive-answer/SKILL.md) | MCP 查询与引用规则，按需加载 |

每套包含技能正文、参考资料和 `.platform/skill.json`。在服务工程目录生成导入包：

```sh
uv run python -m examples.skills.prepare
```

输出为 `.local/examples/skills/text-brief.zip` 和 `.local/examples/skills/archive-answer.zip`；可用 `--output` 指定目录。ZIP 由源码确定生成，不重复提交二进制副本。在技能管理页上传 ZIP，填写本地编码与负责人，并显式绑定本渠道的工具版本。导入生成独立草稿，缺少依赖时不能冻结、发布或加载。

Agent、提示词、依赖清单和输入样本见 [Agent 配置](../agents/README.md)，完整操作见 [配置交付](../../docs/configuration.md#configuration-delivery)。样例用于验证配置流程，业务政策和真实数据由接入方提供。
