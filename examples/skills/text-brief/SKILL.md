---
name: text-brief
description: 将输入文本整理为标题、摘要和关键词。
---

只根据用户提供的文本生成结果。标题不超过二十个字；summary 保留核心事实；keywords 不重复。输入没有足够信息时返回 NEEDS_INPUT，并在 warnings 中说明缺项。不得编造人物、数值或来源。

遵守[输出约定](references/output.md)。
