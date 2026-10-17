"""候选提示词命名、步骤绑定校验与确定性保存标识，预览不提前创建资源。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.agents.access import LockedAuthorization
from creativity_service.modules.agents.assistance_schemas import (
    AssistanceOutput,
    AssistancePromptDraft,
    AssistanceSavedPrompt,
)
from creativity_service.modules.agents.schemas import AgentDefinition
from creativity_service.modules.agents.validation import validate_prompt_dependencies
from creativity_service.modules.prompts.creation import NewPrompt, validate_new_prompt
from creativity_service.modules.prompts.schemas import PromptCreate


def prepare_prompt_drafts(output: AssistanceOutput) -> AssistanceOutput:
    proposal = output.data.proposal
    if not output.data.prompt_drafts or proposal is None:
        return output
    drafts = []
    model_steps = [step.key for step in proposal.definition.steps if step.kind == "model"]
    for draft in output.data.prompt_drafts:
        # 续接对话可能已带旧名称前缀，始终按本轮智能体名称标记来源。
        topic = draft.name.rsplit("抽象的", 1)[-1].removesuffix("提示词").strip()
        name = f"{proposal.name}抽象的{topic}提示词"
        if not topic or len(name) > 128:
            raise ServiceError(
                "ASSISTANCE_INVALID", "请缩短智能体或提示词主题名称，完整名称不能超过 128 字", 422
            )
        # 兼容单模型步骤的旧候选；多步骤缺少目标时交给生成校验修正，不能猜测绑定。
        step_key = draft.step_key or (model_steps[0] if len(model_steps) == 1 else None)
        drafts.append(draft.model_copy(update={"name": name, "step_key": step_key}))
    return output.model_copy(
        update={"data": output.data.model_copy(update={"prompt_drafts": drafts})}
    )


def validate_prompt_drafts(output: AssistanceOutput) -> None:
    proposal, drafts = output.data.proposal, output.data.prompt_drafts
    if not drafts:
        return
    if proposal is None:
        raise ServiceError("ASSISTANCE_INVALID", "待补充信息时不能创建提示词", 422)
    steps = {step.key for step in proposal.definition.steps if step.kind == "model"}
    names: set[str] = set()
    targets: set[str] = set()
    for draft in drafts:
        if draft.step_key is None or draft.step_key not in steps:
            raise ServiceError(
                "ASSISTANCE_INVALID", "每个提取的提示词必须指定方案中已有的模型步骤", 422
            )
        if draft.step_key in targets:
            raise ServiceError(
                "ASSISTANCE_INVALID", "同一模型步骤只能关联一个提示词，请合并该步骤的提示词", 422
            )
        targets.add(draft.step_key)
        if draft.name in names:
            raise ServiceError("ASSISTANCE_INVALID", "提取的提示词主题重复，请合并或区分用途", 422)
        names.add(draft.name)
        validate_new_prompt(
            PromptCreate(prompt_code="assistance", name=draft.name, purpose=draft.purpose),
            draft.content,
        )
        if not (
            draft.content.instruction_blocks.system.strip()
            or draft.content.instruction_blocks.output_requirements.strip()
            or any(message.template.strip() for message in draft.content.message_templates)
        ):
            raise ServiceError("ASSISTANCE_INVALID", "提取的提示词内容不能为空", 422)
        validate_prompt_dependencies(draft.content, proposal.definition)


def require_prompt_publication(
    policy: LockedAuthorization, context: AuthContext, identifiers: list[str]
) -> None:
    for identifier in identifiers:
        policy.require(context, "release:publish", "prompt", identifier)
        policy.require(context, "run:create", "prompt", identifier)


def bind_prompt_drafts(
    definition: AgentDefinition, drafts: list[AssistancePromptDraft], prompts: list[NewPrompt]
) -> AgentDefinition:
    """标识只由服务端生成；关联落到模型步骤，并由定义自动汇总为依赖。"""
    targets = {
        draft.step_key: prompt.identifier for draft, prompt in zip(drafts, prompts, strict=True)
    }
    return definition.model_copy(
        update={
            "steps": tuple(
                step.model_copy(update={"prompt_id": targets[step.key]})
                if step.key in targets
                else step
                for step in definition.steps
            )
        }
    )


def new_prompts(
    context: AuthContext, run_id: str, drafts: list[AssistancePromptDraft]
) -> list[NewPrompt]:
    result = []
    for index, draft in enumerate(drafts):
        suffix = digest([context.scope.model_dump(), run_id, "prompt", index])[:32]
        result.append(
            NewPrompt(
                "prompt_" + suffix,
                "audit_" + suffix,
                PromptCreate(
                    prompt_code="assisted_" + suffix, name=draft.name, purpose=draft.purpose
                ),
                draft.content,
            )
        )
    return result


def saved_prompts(prompts: list[NewPrompt]) -> list[AssistanceSavedPrompt]:
    return [AssistanceSavedPrompt(prompt_id=p.identifier, name=p.resource.name) for p in prompts]
