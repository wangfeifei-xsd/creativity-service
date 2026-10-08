"""从统一机器清单生成可审阅的模块字段归档。"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs/data-model"


def field_table(columns):
    lines = [
        "| 字段 | 存储类型（长度/精度） | 中文说明 | 业务必填 | 取值来源 | 敏感级别 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| `{c['name']}` | `{c['type']}` | {c['comment']} | {'是' if c['required'] else '否'} | "
        f"{c['source']} | {c['sensitivity']} |"
        for c in columns
    ]
    return lines


def render():
    catalog = json.loads((ARCHIVE / "catalog.json").read_text())
    outputs = {}
    index = [
        "# Creativity P0 数据模型索引",
        "",
        f"模型版本 **{catalog['model_version']}**；需求基线 **{catalog['requirements_version']}**；"
        f"技术基线 **{catalog['technical_version']}**。",
        "",
        "本档案覆盖全部 P0 持久化对象。机器清单为 [catalog.json](catalog.json)，"
        "字段文档由 `scripts/render_data_model.py` 生成。"
        "共享基础的代码定义位于 `core/database/baseline_v0001.json`；"
        "各模块归档中的 `revision` 保留原始修订来源，供模型归属与一致性检查使用。"
        "[初始基线](../../alembic/versions/0001_initial.py) 冻结至 "
        "`0034_admission_indexes`；后续增量迁移依次升级至 "
        "`0046_mcp_plain_credentials`。"
        "开发规范引用 [rule.md](../../../rule.md)。",
        "",
        "空库初始化按顺序执行表结构 [sql/init.sql](../../sql/init.sql) 和"
        "数据 [sql/init_data.sql](../../sql/init_data.sql)；"
        "执行、维护与验证方法见 [初始化说明](../../sql/README.md)。",
        "",
        "需求 15–17 为可选配置示例，原 12 张领域表设计已撤销，不属于待建库清单。"
        "平台上下文统一为渠道、环境与主体；"
        "业务工具统一由 MCP 接入，固定业务 HTTP 协议已移除。",
        "",
        "对象逻辑标识（如 run_id、conversation_id、version_id）在所属表统一物理存为 `id`；"
        "关联字段保留业务名称。渠道主档的 `id` 与 `channel_id` 相等。"
        "业务必填由服务入口验证，所有普通列均显式赋值。"
        "JSONB 中的类型化内容由所属模块 schema 校验；敏感级别按来源可向上提升。",
        "",
        "[关系与生命周期](relations.md) · [服务不变量](service-invariants.md) · "
        "[存储职责](storage-map.md) · [迁移兼容](changes.md)",
        "",
        "| 表/对象 | 所属模块 | 需求 | 状态 | 负责方案 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for module, info in catalog["modules"].items():
        lines = [
            f"# {info['title']}模型",
            "",
            f"模型版本 {catalog['model_version']}；负责方案 {info['owner']}；需求 "
            f"[{info['requirements']}](../../../../需求文档/{info['requirements']})。"
            "总索引见 [README](../README.md)。",
            "",
        ]
        for t in (t for t in catalog["tables"] if t["module"] == module):
            index.append(
                f"| `{t['name']}` | [{info['title']}](modules/{module}.md) | "
                f"{info['requirements']} | {t['status']} | {info['owner']} |"
            )
            lines += [
                f"## {t['name']}",
                "",
                f"{t['comment']}。状态：{t['status']}；归属：{t['scope']}；"
                f"归档修订：{t['revision'] or '由所属方案新增'}。",
                "",
                *field_table(t["columns"]),
                "",
            ]
            lines += [
                "普通索引："
                + ("；".join("`(" + ", ".join(i) + ")`" for i in t["indexes"]) or "无")
                + "。",
                "",
            ]
            if t["control_purpose"]:
                lines += [
                    f"控制面用途：`{t['control_purpose']}`；账号/角色身份引用不赋予其他渠道数据访问权。",
                    "",
                ]
        for name, payload in catalog["version_payloads"].get(module, {}).items():
            lines += [
                f"## 版本内容结构：{name}",
                "",
                "存于公共 `resource_versions.content`；数组项结构按名称单独列出。"
                "父版本、依赖与发布映射只保存一份。",
                "",
                *field_table(payload),
                "",
            ]
        for obj in (o for o in catalog["kv_objects"] if o["module"] == module):
            index.append(
                f"| `{obj['name']}`（{obj['storage']}） | [{info['title']}](modules/{module}.md) | "
                f"{info['requirements']} | {obj.get('status', '设计基线')} | {info['owner']} |"
            )
            lines += [
                f"## {obj['name']}",
                "",
                f"存储：{obj['storage']}；无 PostgreSQL 副本。",
                "",
                *field_table(obj["columns"]),
                "",
            ]
        outputs[ARCHIVE / "modules" / f"{module}.md"] = "\n".join(lines)
    index += [
        "",
        "复用映射：渠道审计与账号审计共用 `audit_events`；"
        "模型价格归用量模块 `price_versions`；"
        "资源内容、依赖、发布映射和运行快照共用公共表；会话删除任务复用方案 25；"
        "任意 Agent 的结果使用通用运行与产物模型，业务领域对象由源系统维护。"
        "前端工作区没有独立权限或导航真值表。",
        "",
        "后续方案开始编码前检查对应对象已在本索引中。"
        "新增字段、关系、索引或状态，先修订机器清单与关系/不变量，再在同批提交实现与迁移。"
        "验收 26 核验实际 schema，发布 27 将本目录复制成不可变模型快照。",
        "",
    ]
    outputs[ARCHIVE / "README.md"] = "\n".join(index)
    return outputs


def main():
    parser = argparse.ArgumentParser(description="生成模型归档")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, content in render().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"模型归档过期：{path.name}")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
