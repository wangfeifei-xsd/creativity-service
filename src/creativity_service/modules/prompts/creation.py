"""提示词及初始配置的原子批量创建，供管理入口和智能协助共用。"""

import re
from dataclasses import dataclass

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import UnitOfWork
from creativity_service.core.database.inserts import InsertBatch
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.agents.access import LockedAuthorization, locked_policy
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.prompts.rendering import validate_content
from creativity_service.modules.prompts.repositories import repository
from creativity_service.modules.prompts.schemas import PromptContent, PromptCreate
from creativity_service.modules.resources.configuration import configuration_values


@dataclass(frozen=True)
class NewPrompt:
    identifier: str
    audit_id: str
    resource: PromptCreate
    content: PromptContent


def validate_new_prompt(body: PromptCreate, content: PromptContent) -> None:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", body.prompt_code):
        raise ServiceError(
            "PROMPT_CODE_INVALID",
            "提示词编码须以字母开头，仅包含字母、数字、点、下划线或短横线",
            422,
        )
    if not body.name.strip() or not body.purpose.strip():
        raise ServiceError("PROMPT_INVALID", "名称和用途不能为空", 422)
    validate_content(content)


def publication_ids(scope: Scope, prompt: NewPrompt) -> tuple[str, str]:
    return (
        digest([scope.channel_id, scope.environment, "prompt", prompt.identifier]),
        "audit_" + digest([prompt.audit_id, "publish"])[:32],
    )


def creation_keys(
    scope: Scope, prompts: list[NewPrompt], *, publish: bool = False
) -> list[ResourceKey]:
    keys = [content_key(scope), policy_key(scope.channel_id), policy_key("system")]
    for prompt in prompts:
        keys.extend(
            [
                ResourceKey(scope.channel_id, "prompt-code", (prompt.resource.prompt_code,)),
                record_key(scope.channel_id, "prompts", prompt.identifier),
                record_key(scope.channel_id, "resource_versions", prompt.identifier),
                record_key(scope.channel_id, "audit_events", prompt.audit_id),
            ]
        )
        if publish:
            mapping_id, audit_id = publication_ids(scope, prompt)
            keys.extend(
                [
                    record_key(scope.channel_id, "release_mappings", mapping_id),
                    record_key(scope.channel_id, "audit_events", audit_id),
                ]
            )
    return keys


async def create_prompts_in(
    uow: UnitOfWork, context: AuthContext, prompts: list[NewPrompt], *, publish: bool = False
) -> None:
    """入口已复核创建及适用的发布权限；新资源校验和发布映射在同一事务批量写入。"""
    if not prompts:
        return
    codes = [prompt.resource.prompt_code for prompt in prompts]
    if len(set(codes)) != len(codes) or len({p.identifier for p in prompts}) != len(prompts):
        raise ServiceError("PROMPT_CODE_CONFLICT", "提示词编码重复", 409)
    for prompt in prompts:
        uow.require_lock(
            ResourceKey(context.scope.channel_id, "prompt-code", (prompt.resource.prompt_code,))
        )
        validate_new_prompt(prompt.resource, prompt.content)
    repo = repository("prompts", context.scope)
    if await repository("prompts", context.scope, include_deleted=True).find_many(
        uow.connection, "prompt_code", codes
    ):
        raise ServiceError("PROMPT_CODE_CONFLICT", "提示词编码已存在", 409)
    await DeletionGuard(context.scope).check(
        uow,
        [
            ContentRef(kind, prompt.identifier)
            for prompt in prompts
            for kind in ("prompt", "version")
        ],
    )
    pending = InsertBatch(uow)
    await repo.add_many(
        uow,
        {
            prompt.identifier: {
                **prompt.resource.model_dump(),
                "owner": context.principal_id,
                "status": "ACTIVE",
            }
            for prompt in prompts
        },
        pending=pending,
    )
    await repository("resource_versions", context.scope).add_many(
        uow,
        {
            prompt.identifier: {
                **configuration_values(
                    "prompt",
                    prompt.identifier,
                    prompt.content.model_dump(mode="json"),
                    [],
                    {},
                    context.principal_id,
                ),
                "state": "PUBLISHED" if publish else "DRAFT",
            }
            for prompt in prompts
        },
        pending=pending,
    )
    if publish:
        # 新建资源必为启用、无依赖的合法模板；发布不要求调试或模型调用。
        await repository("release_mappings", context.scope).add_many(
            uow,
            {
                publication_ids(context.scope, prompt)[0]: {
                    "resource_type": "prompt",
                    "resource_id": prompt.identifier,
                    "version_id": prompt.identifier,
                    "published_by": context.principal_id,
                    "release_note": "智能协助自动发布",
                }
                for prompt in prompts
            },
            pending=pending,
        )
    for prompt in prompts:
        await append_audit(
            uow,
            context,
            prompt.audit_id,
            "prompt.create",
            "prompt",
            prompt.identifier,
            pending=pending,
        )
        if publish:
            await append_audit(
                uow,
                context,
                publication_ids(context.scope, prompt)[1],
                "resource.publish",
                "prompt",
                prompt.identifier,
                pending=pending,
            )
    await pending.flush()


async def require_creation(
    uow: UnitOfWork,
    context: AuthContext,
    contents: list[PromptContent],
    *,
    policy: LockedAuthorization | None = None,
) -> None:
    policy = policy if policy is not None else await locked_policy(uow, context)
    policy.require(context, "prompt:manage", "prompt", "new")
    if any(
        variable.default is not None and variable.sensitivity in {"sensitive", "secret"}
        for content in contents
        for variable in content.variables
    ):
        policy.require(context, "data:read_sensitive", "prompt", "new")
