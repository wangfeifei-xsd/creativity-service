"""全量分页依据原始工具响应推进，末页后才向后续节点提供全部页面。"""

from typing import Any

from creativity_service.core.primitives import ServiceError, canonical_json
from creativity_service.modules.agents.schemas import StepPagination


def accumulate_page(
    raw: dict[str, Any], previous: dict[str, Any] | None, progress: dict[str, Any]
) -> dict[str, Any]:
    output = {
        "complete": progress["complete"],
        "received": progress["received"],
        "page_count": progress["pages"],
        "total": progress["total"],
        "pages": [*(previous or {}).get("pages", []), raw],
    }
    if len(canonical_json(output)) > 1048576:
        raise ServiceError("DATA_INCOMPLETE", "累计查询结果超过 1 MiB，数据不完整", 429)
    return output


def read_path(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ServiceError("PAGINATION_INVALID", "分页响应缺少约定字段，数据不完整", 422)
        current = current[part]
    return current


def page_arguments(
    policy: StepPagination, values: dict[str, Any], prior: dict[str, Any]
) -> dict[str, Any]:
    result = dict(values)
    if prior:
        if prior["complete"]:
            raise ServiceError("PAGINATION_INVALID", "已结束的全量分页不能重复进入", 422)
        result[policy.cursor_input] = prior["cursor"]
        fixed = {k: v for k, v in result.items() if k != policy.cursor_input}
        if fixed != prior["arguments"]:
            raise ServiceError("PAGINATION_INVALID", "续页时查询条件发生变化，数据不完整", 422)
    elif result.get(policy.cursor_input) not in (None, ""):
        raise ServiceError("PAGINATION_INVALID", "全量查询必须从首页开始", 422)
    return result


def check_page(
    policy: StepPagination, raw: dict[str, Any], values: dict[str, Any], prior: dict[str, Any]
) -> dict[str, Any]:
    items = read_path(raw, policy.items_path)
    more = read_path(raw, policy.has_more_path)
    if not isinstance(items, list) or type(more) is not bool:
        raise ServiceError("PAGINATION_INVALID", "分页列表或续页标记类型错误，数据不完整", 422)
    if policy.count_path:
        count = read_path(raw, policy.count_path)
        if type(count) is not int or count != len(items):
            raise ServiceError("PAGINATION_INVALID", "返回条数与项目列表不一致，数据不完整", 422)
    cursor = read_path(raw, policy.cursor_path) if more else None
    seen = list(prior.get("seen", []))
    if more:
        if (
            not isinstance(cursor, str)
            or not cursor.strip()
            or len(cursor) > 4096
            or cursor in seen
        ):
            raise ServiceError(
                "PAGINATION_INVALID", "续页游标缺失、重复或不符合约定，数据不完整", 422
            )
        seen.append(cursor)
    received = prior.get("received", 0) + len(items)
    if received > policy.max_items:
        raise ServiceError("DATA_INCOMPLETE", "查询条数达到运行上限，数据不完整", 429)
    total = read_path(raw, policy.total_path) if policy.total_path else None
    if policy.total_path and (
        type(total) is not int
        or total < received
        or (prior and prior["total"] != total)
        or (not more and total != received)
    ):
        raise ServiceError("PAGINATION_INVALID", "项目总数与累计列表不一致，数据不完整", 422)
    return {
        "cursor": cursor,
        "has_more": more,
        "complete": not more,
        "seen": seen,
        "received": received,
        "pages": prior.get("pages", 0) + 1,
        "total": total,
        "arguments": {k: v for k, v in values.items() if k != policy.cursor_input},
    }
