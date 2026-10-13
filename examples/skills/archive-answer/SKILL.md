---
name: archive-answer
description: 根据授权的 MCP 档案查询结果回答问题。
---

根据本步骤输入中的 notes 生成 answer 和 citations。只引用源端实际返回的摘录；无资料时返回 INSUFFICIENT_DATA，部分资料时返回 PARTIAL。未提供 evidence_refs 时返回空数组，不编造来源引用。工具完整来源与观测信息由执行记录保存；技能版本和配置摘要不表示业务数据仍然有效。

技能无法授予工具权限。权威事实及访问校验由源 MCP 执行，参考资料只规定输出方式。遵守[引用规则](references/citation.md)。
