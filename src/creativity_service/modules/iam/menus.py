"""服务端维护菜单目录；页面可见性不替代接口与资源授权。"""

from typing import Any, Literal

from pydantic import Field
from sqlalchemy import delete, select, union_all
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext, ControlScope
from creativity_service.core.contracts import NavigationGroup, NavigationItem
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import Contract, Identifier, Revision, ServiceError, new_id
from creativity_service.modules.iam.accounts import current_actor
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.authorization import require_platform
from creativity_service.modules.iam.menu_catalog import PAGES, PROTECTED_PAGES
from creativity_service.modules.iam.repositories import TABLES, IdentityRepository, policy_key, save
from creativity_service.modules.iam.roles import (
    ACTION_NAMES,
    PLATFORM_ACTIONS,
    PLATFORM_ONLY_ACTIONS,
)


class MenuSave(Contract):
    revision: Revision | None = None
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["DIR", "MENU", "BUTTON"]
    parent_id: Identifier | None = None
    page_key: str | None = Field(default=None, max_length=64)
    action_key: str | None = Field(default=None, max_length=64)
    workspace: Literal["platform", "channel", "both"] = "both"
    sort_order: int = Field(default=0, ge=0, le=10000)
    visible: bool = True
    active: bool = True


async def menu_rows(connection: AsyncConnection) -> list[dict[str, Any]]:
    table = TABLES["iam_menus"]
    result = (
        await connection.execute(
            select(table)
            .where(table.c.channel_id == "system")
            .order_by(table.c.sort_order, table.c.id)
            .limit(1001)
        )
    ).mappings()
    values = [dict(row) for row in result]
    if len(values) > 1000:
        raise ServiceError("MENU_LIMIT", "菜单目录超过管理上限", 409)
    return values


def ancestors(row: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen = {row["id"]}
    parent = row["parent_id"]
    while parent:
        if parent in seen or parent not in catalog or len(result) >= 8:
            raise ServiceError("MENU_PARENT_INVALID", "菜单父级不存在、形成循环或层级超过八级", 422)
        seen.add(parent)
        item = catalog[parent]
        result.insert(0, item)
        parent = item["parent_id"]
    return result


def compatible(workspace: str, target: str) -> bool:
    return workspace == "both" or workspace == target


def menu_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "kind_label": {"DIR": "目录", "MENU": "菜单", "BUTTON": "按钮"}[row["kind"]],
        "workspace_label": {"platform": "平台", "channel": "渠道", "both": "平台与渠道"}[
            row["workspace"]
        ],
        "page_name": PAGES[row["page_key"]][0] if row["page_key"] in PAGES else None,
        "action_name": ACTION_NAMES.get(row["action_key"]),
        "status_label": "启用" if row["active"] else "停用",
        "protected": row["page_key"] in PROTECTED_PAGES,
    }


class MenuService:
    def __init__(self, repository: IdentityRepository) -> None:
        self.repository = repository

    async def list(self, session: AdminSession) -> list[dict[str, Any]]:
        self.authorize(session)
        async with self.repository.engine.connect() as connection:
            return [menu_view(row) for row in await menu_rows(connection)]

    @staticmethod
    def authorize(session: AdminSession) -> None:
        if isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请在平台工作区管理菜单", 403)
        require_platform(session.account, "menu:manage")

    def options(self, session: AdminSession) -> dict[str, Any]:
        self.authorize(session)
        return {
            "pages": [
                {"value": key, "label": value[0], "workspace": value[1]}
                for key, value in PAGES.items()
            ],
            "actions": [{"value": key, "label": name} for key, name in ACTION_NAMES.items()],
        }

    @staticmethod
    def validate(row: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> None:
        if not row["name"].strip():
            raise ServiceError("VALIDATION_ERROR", "菜单名称不能为空", 422)
        if row["kind"] == "MENU":
            if row["page_key"] not in PAGES or row["action_key"]:
                raise ServiceError(
                    "MENU_PAGE_INVALID", "请选择已注册页面，页面节点不能绑定按钮动作", 422
                )
            if not compatible(PAGES[row["page_key"]][1], row["workspace"]):
                raise ServiceError("MENU_SCOPE_INVALID", "页面与工作区不匹配", 422)
        elif row["kind"] == "BUTTON":
            if row["action_key"] not in ACTION_NAMES or row["page_key"]:
                raise ServiceError(
                    "MENU_ACTION_INVALID", "请选择有效操作权限，按钮不能绑定页面", 422
                )
            if (row["action_key"] in PLATFORM_ONLY_ACTIONS and row["workspace"] != "platform") or (
                row["action_key"] not in PLATFORM_ACTIONS and row["workspace"] != "channel"
            ):
                raise ServiceError("MENU_SCOPE_INVALID", "操作权限与工作区不匹配", 422)
        elif row["page_key"] or row["action_key"]:
            raise ServiceError("MENU_KIND_INVALID", "目录不能绑定页面或按钮权限", 422)
        parents = ancestors(row, catalog)
        if parents:
            parent = parents[-1]
            if parent["kind"] == "BUTTON" or (row["kind"] != "BUTTON" and parent["kind"] != "DIR"):
                raise ServiceError(
                    "MENU_PARENT_INVALID", "目录下可添加菜单，菜单下只能添加按钮", 422
                )
            if any(not compatible(p["workspace"], row["workspace"]) for p in parents):
                raise ServiceError("MENU_SCOPE_INVALID", "父级工作区不能小于子级范围", 422)
        if row["page_key"] in PROTECTED_PAGES and any(
            not n["active"] or not n["visible"] for n in [*parents, row]
        ):
            raise ServiceError("MENU_PROTECTED", "账号、角色和菜单管理入口不能隐藏或停用", 409)
        for other in catalog.values():
            if other["id"] == row["id"]:
                continue
            if (row["page_key"] and row["page_key"] == other["page_key"]) or (
                row["parent_id"] == other["parent_id"] and row["name"] == other["name"]
            ):
                raise ServiceError("MENU_DUPLICATE", "页面已绑定或同级菜单名称重复", 409)

    async def save(
        self, session: AdminSession, body: MenuSave, identifier: str | None = None
    ) -> dict[str, Any]:
        self.authorize(session)
        creating = identifier is None
        identifier = identifier or new_id("menu")
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            ControlScope(purpose="roles", actor_id=session.account.id),
            [
                policy_key("system"),
                record_key("system", "iam_menus", identifier),
                record_key("system", "audit_events", event_id),
            ],
        ) as uow:
            await current_actor(uow, session, "menu:manage")
            catalog = {r["id"]: r for r in await menu_rows(uow.connection)}
            old = catalog.get(identifier)
            if not creating and not old:
                raise ServiceError("NOT_FOUND", "菜单不存在", 404)
            if creating and len(catalog) >= 1000:
                raise ServiceError("MENU_LIMIT", "菜单目录最多一千个节点", 409)
            values = body.model_dump(exclude={"revision"})
            values["name"] = body.name.strip()
            if (
                old
                and old["page_key"] in PROTECTED_PAGES
                and (body.page_key != old["page_key"] or body.kind != "MENU")
            ):
                raise ServiceError("MENU_PROTECTED", "管理入口不能改变页面绑定", 409)
            catalog[identifier] = {"id": identifier, **values}
            # 全树在同一策略锁下验证，修改父目录也须保证已有子节点仍合法。
            for row in catalog.values():
                self.validate(row, catalog)
            result = await save(uow, "iam_menus", identifier, values, body.revision)
            await append_event(
                uow,
                event_id,
                session.account.id,
                session.context.request_id,
                "menu:create" if creating else "menu:update",
                "menu",
                identifier,
                list(values),
                target_name=values["name"],
            )
        return menu_view(result)

    async def remove(self, session: AdminSession, identifier: str, revision: int) -> None:
        self.authorize(session)
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            ControlScope(purpose="roles", actor_id=session.account.id),
            [
                policy_key("system"),
                record_key("system", "iam_menus", identifier),
                record_key("system", "audit_events", event_id),
            ],
        ) as uow:
            await current_actor(uow, session, "menu:manage")
            catalog = {r["id"]: r for r in await menu_rows(uow.connection)}
            old = catalog.get(identifier)
            if not old:
                raise ServiceError("NOT_FOUND", "菜单不存在", 404)
            if old["revision"] != revision:
                raise ServiceError("REVISION_CONFLICT", "菜单已变化，请刷新后重试", 409)
            if old["page_key"] in PROTECTED_PAGES:
                raise ServiceError("MENU_PROTECTED", "账号、角色和菜单管理入口不能删除", 409)
            # 平台菜单治理只核查引用是否存在，不读取其他渠道的业务内容。
            referenced = await uow.connection.scalar(
                union_all(
                    select(TABLES["custom_roles"].c.id).where(
                        TABLES["custom_roles"].c.menu_ids.contains([identifier])
                    ),
                    select(TABLES["builtin_roles"].c.id).where(
                        TABLES["builtin_roles"].c.channel_id == "system",
                        TABLES["builtin_roles"].c.menu_ids.contains([identifier]),
                    ),
                ).limit(1)
            )
            if referenced or any(r["parent_id"] == identifier for r in catalog.values()):
                raise ServiceError("MENU_REFERENCED", "菜单仍有子节点或关联角色，请先处理引用", 409)
            table = TABLES["iam_menus"]
            await uow.connection.execute(
                delete(table).where(table.c.channel_id == "system", table.c.id == identifier)
            )
            await append_event(
                uow,
                event_id,
                session.account.id,
                session.context.request_id,
                "menu:delete",
                "menu",
                identifier,
                ["name"],
                target_name=old["name"],
            )


def navigation(
    values: list[dict[str, Any]], actions: frozenset[str], workspace: str, selected: set[str] | None
) -> list[NavigationItem]:
    catalog = {row["id"]: row for row in values}
    result = []
    for row in values:
        page = PAGES.get(row["page_key"])
        if row["kind"] != "MENU" or page is None:
            continue
        parents = ancestors(row, catalog)
        if not compatible(page[1], workspace) or not set(page[2]) & actions:
            continue
        if any(
            not n["active"] or not n["visible"] or not compatible(n["workspace"], workspace)
            for n in [*parents, row]
        ):
            continue
        if selected is not None and row["id"] not in selected:
            continue
        result.append(
            (
                tuple((n["sort_order"], n["id"]) for n in [*parents, row]),
                NavigationItem(
                    navigation_key=row["page_key"],
                    label=row["name"],
                    ancestors=[NavigationGroup(key=p["id"], label=p["name"]) for p in parents],
                ),
            )
        )
    return [item for _, item in sorted(result, key=lambda pair: pair[0])]
