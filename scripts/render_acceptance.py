"""从源需求及本次 JUnit 结果生成追踪表，缺证据和跳过不计作通过。"""

import argparse
import ast
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from scripts.acceptance_matrix import DEFERRED, LINKS, PARTIAL, PLANS

ROOT = Path(__file__).resolve().parents[1]


def source_requirements():
    result = {}
    for source in sorted((ROOT.parent / "需求文档").glob("*.md")):
        if int(source.name[:2]) > 14:
            continue
        for number, line in enumerate(source.read_text().splitlines(), 1):
            match = re.match(r"\| ([A-Z]+-[FA]\d+) \| (.*?) \|", line)
            if match:
                identifier, description = match.groups()
                if identifier in result:
                    raise ValueError(f"源需求编号重复：{identifier}")
                result[identifier] = {
                    "id": identifier,
                    "source": "../需求文档/" + source.name,
                    "line": number,
                    "description": description,
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
    return result


def mappings():
    functions = defaultdict(list)
    for path in (ROOT / "tests").rglob("test*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test_"
            ):
                functions[node.name].append(f"{path.relative_to(ROOT)}::{node.name}")
    result = {}
    for line in LINKS.strip().splitlines():
        identifiers, names = line.split(" = ")
        tests = []
        for name in names.split():
            if len(functions[name]) != 1:
                raise ValueError(f"用例不存在或不明确：{name}")
            tests.extend(functions[name])
        for identifier in identifiers.split():
            if identifier in result:
                raise ValueError(f"需求重复映射：{identifier}")
            result[identifier] = tests
    requirements = source_requirements()
    if set(result) | set(DEFERRED) != set(requirements):
        raise ValueError(
            f"缺少映射：{set(requirements) - set(result) - set(DEFERRED)}；"
            f"源中不存在：{(set(result) | set(DEFERRED)) - set(requirements)}"
        )
    return result


def results(folder):
    outcomes = {}
    for path in sorted((folder / "logs").glob("*.xml"), key=lambda value: value.stat().st_mtime_ns):
        for case in ET.parse(path).getroot().iter("testcase"):
            name = case.get("name", "")
            classname = case.get("classname", "")
            if not classname.startswith("tests."):
                continue
            node = classname.replace(".", "/") + ".py::" + name
            state = "passed"
            if case.find("failure") is not None or case.find("error") is not None:
                state = "failed"
            elif case.find("skipped") is not None:
                state = "skipped"
            outcomes[node] = {"state": state, "evidence": str(path.relative_to(folder))}
    return outcomes


def classify(nodes, outcomes, collected):
    if any(
        not any(name == node or name.startswith(node + "[") for name in collected) for node in nodes
    ):
        return "缺少收集记录", []
    expected = [
        name
        for name in collected
        if any(name == node or name.startswith(node + "[") for node in nodes)
    ]
    if not expected:
        return "缺少收集记录", []
    evidence = [
        {"node": name, **outcomes.get(name, {"state": "missing", "evidence": None})}
        for name in expected
    ]
    states = {item["state"] for item in evidence}
    if "failed" in states:
        status = "用例失败"
    elif "missing" in states or "skipped" in states:
        status = "证据未齐"
    else:
        status = "关联用例通过"
    return status, evidence


def render(folder):
    linked = mappings()
    outcomes = results(folder)
    collection = folder / "logs/collection.txt"
    collected = (
        [
            line
            for line in collection.read_text().splitlines()
            if line.startswith("tests/") and "::" in line
        ]
        if collection.exists()
        else []
    )
    records = []
    for identifier, source in source_requirements().items():
        tests = linked.get(identifier, [])
        status, evidence = classify(tests, outcomes, collected)
        if identifier in DEFERRED:
            status = "后续阶段"
        elif identifier == "MOD-A01":
            status = "真实连接阻断"
        records.append(
            {
                **source,
                "plans": PLANS[identifier.split("-")[0]],
                "priority": "P1" if identifier in DEFERRED else "P0",
                "tests": tests,
                "status": status,
                "evidence": evidence,
                "boundary": DEFERRED.get(identifier)
                or PARTIAL.get(identifier)
                or "本地受控验收；范围以关联用例断言和环境清单为准。",
            }
        )
    summary = dict(Counter(row["status"] for row in records))
    payload = {"schema_version": 1, "summary": summary, "requirements": records}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "traceability.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# 26 需求追踪表",
        "",
        "源编号直接读取需求总纲及 01–14；15–17 配置示例不计入平台必测。"
        "通用规范见 [rule.md](../../../rule.md)。",
        "",
        "“关联用例通过”仅表示本次记录中的全部参数用例成功，"
        "不等于真实供应商兼容或正式上线验收完成。跳过、缺记录和失败均不计通过；"
        "F 编号的组合条款仍须结合断言和下方范围审阅。",
        "",
        "统计：" + "；".join(f"{key} {value}" for key, value in summary.items()) + "。",
        "",
        "| 源编号 | 需求 | 负责方案 | 当前状态 | 自动用例及范围 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in records:
        source = "../../../需求文档/" + Path(row["source"]).name
        references = "<br>".join(
            f"[{node.split('::')[-1]}](../../{node.split('::')[0]})" for node in row["tests"]
        )
        # Markdown 路径从验收目录回到服务端需两层；原始节点与日志保存在 JSON。
        description = row["description"].replace("|", "\\|")
        lines.append(
            f"| [{row['id']}]({source}) | {description} | {row['plans']} | {row['status']} | "
            f"{references}<br>{row['boundary']} |"
        )
    (folder / "traceability.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, ensure_ascii=False))
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/acceptance")
    args = parser.parse_args()
    render(args.output.resolve())


if __name__ == "__main__":
    main()
