---
name: archive-answer
description: 根据授权的 MCP 档案查询结果回答问题。
---

根据查询步骤返回的 notes 生成 answer 和 citations。只引用源端实际返回的摘录；无资料时返回 INSUFFICIENT_DATA，部分资料时返回 PARTIAL。工具返回的 source_version、observed_at 和 evidence_refs 表达本次数据来源与新鲜度；技能版本和配置摘要不表示业务数据仍然有效。

技能无法授予工具权限。权威事实及访问校验由源 MCP 执行，参考资料只规定输出方式。遵守[引用规则](references/citation.md)。
