"""共享资源测试夹具通过现行发布入口建立可用依赖。"""

from creativity_service.modules.resources.schemas import ResourceMutation
from creativity_service.modules.resources.services import ResourceManagement


async def publish_resource(env, kind, identifier):
    validators = {
        "model_route": env.models.routing,
        "prompt": env.prompts.versions.validator,
        "skill": env.skills.versions.validator,
        "tool": env.tools.management.versions.validator,
    }
    resources = ResourceManagement(env.engine, env.iam.authorization, validators)
    item = (await resources.summaries(env.context, kind, [identifier]))[0]
    return await resources.mutate(
        env.context,
        kind,
        identifier,
        "publish",
        ResourceMutation(
            revision=item.revision, configuration_revision=item.configuration_revision
        ),
    )
