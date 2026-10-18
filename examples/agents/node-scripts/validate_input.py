"""单一脚本接收入参，校验并整理为可直接流转或结束的节点出参。"""


def transform(value, context):
    inputs = {key: item.strip() if isinstance(item, str) else item for key, item in value.items()}
    missing = [key for key, item in inputs.items() if item is None or item == ""]
    success = not missing
    return {
        "business_status": "COMPLETED" if success else "NEEDS_INPUT",
        "schema_version": "1.0",
        "data": {
            "success": success,
            "conclusion": "校验通过" if success else "以下参数不能为空：" + "、".join(missing),
            "inputs": inputs,
        },
        "warnings": [],
        "evidence_refs": [],
    }
