"""独立脚本核对全量页面的业务租户、项目和规格，确定性选取最低报价。"""

from decimal import Decimal, InvalidOperation


def validate_page(value, tenant_id, seen, cheapest):
    if value.get("tenant_id") != tenant_id:
        raise ValueError("返回租户与查询租户不一致")
    items = value.get("items")
    if not isinstance(items, list) or type(value.get("count")) is not int:
        raise ValueError("项目列表或返回条数不符合约定")
    if value["count"] != len(items):
        raise ValueError("返回条数与项目列表不一致")
    for project in items:
        if (
            not isinstance(project, dict)
            or not isinstance(project.get("id"), str)
            or not project["id"]
        ):
            raise ValueError("项目缺少有效标识")
        if project["id"] in seen:
            raise ValueError("项目重复返回")
        seen.add(project["id"])
        specs = project.get("specs")
        if not isinstance(specs, list) or not specs:
            raise ValueError("项目规格列表不能为空")
        for spec in specs:
            if not isinstance(spec, dict):
                raise ValueError("规格必须是对象")
            for field in ("cpu_cores", "memory_gb"):
                if type(spec.get(field)) is not int or spec[field] <= 0:
                    raise ValueError("规格的核心数和内存须为正整数")
            try:
                price = Decimal(spec["price"])
            except (KeyError, TypeError, InvalidOperation):
                raise ValueError("规格缺少有效报价") from None
            if not isinstance(spec["price"], str) or not price.is_finite() or price < 0:
                raise ValueError("报价须为非负十进制字符串")
            candidate = {
                "project_id": project["id"],
                "price": str(price),
                "cpu_cores": spec["cpu_cores"],
                "memory_gb": spec["memory_gb"],
            }
            if cheapest is None or (price, project["id"], spec["cpu_cores"], spec["memory_gb"]) < (
                Decimal(cheapest["price"]),
                cheapest["project_id"],
                cheapest["cpu_cores"],
                cheapest["memory_gb"],
            ):
                cheapest = candidate
    return cheapest


def transform(value, context):
    try:
        query = value["query"]
        if query.get("complete") is not True:
            raise ValueError("全量查询未完成，数据不完整")
        pages = query["pages"]
        if not isinstance(pages, list) or not pages:
            raise ValueError("缺少完整查询页面")
        seen = set()
        cheapest = None
        for page in pages:
            cheapest = validate_page(page, value["tenant_id"], seen, cheapest)
        if len(seen) != query["received"]:
            raise ValueError("项目总数与累计列表不一致")
        data = {
            "success": True,
            "conclusion": "已校验全部项目及规格",
            "complete": True,
            "project_ids": sorted(seen),
            "cheapest": cheapest,
        }
    except (ValueError, KeyError, TypeError) as error:
        data = {"success": False, "conclusion": str(error), "complete": False}
    return {
        "business_status": "COMPLETED" if data["success"] else "INSUFFICIENT_DATA",
        "schema_version": "1.0",
        "data": data,
        "warnings": [] if data["success"] else ["数据校验未通过，不能给出全量最优结论"],
        "evidence_refs": [],
    }
