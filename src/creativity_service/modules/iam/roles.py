"""首版六类角色的动作上限；独立动作必须在资源授权中明确授予。"""

from typing import Literal, get_args

ACTION_NAMES = {
    "account:manage": "管理账号",
    "role:grant": "授予平台角色",
    "menu:manage": "管理菜单",
    "channel:create": "开通渠道",
    "channel:govern": "治理渠道",
    "channel:manage": "管理渠道",
    "environment:manage": "管理环境",
    "data_scope:manage": "管理数据域",
    "client:manage": "管理接入服务",
    "key:manage": "管理接入凭据",
    "membership:read": "查看渠道成员",
    "membership:manage": "管理渠道成员",
    "grant:read": "查看资源授权",
    "grant:manage": "管理资源授权",
    "model:manage": "管理模型",
    "agent:manage": "管理智能体",
    "prompt:manage": "管理提示词",
    "tool:manage": "管理工具",
    "integration:manage": "管理业务接入",
    "mcp:manage": "管理 MCP 连接",
    "skill:manage": "管理技能",
    "version:edit": "编辑版本",
    "version:freeze": "冻结版本",
    "version:read": "查看版本",
    "run:create": "执行能力",
    "run:approve": "审批运行操作",
    "run:read": "查看运行元数据",
    "run:content": "查看运行内容",
    "feedback:manage": "处理反馈",
    "evaluation:manage": "管理评测",
    "evaluation:read": "查看评测",
    "evaluation:content": "读取评测原文",
    "evaluation:review": "审核评测标签与报告",
    "metric:read": "查看指标",
    "analysis:run": "执行分析",
    "report:read": "查看报告",
    "usage:read": "查看用量",
    "usage:platform": "跨渠道查看用量",
    "budget:manage": "管理预算",
    "audit:read": "查看操作审计",
    "artifact:upload": "上传文件",
    "artifact:download": "下载文件",
    "snapshot:read": "查看运行快照",
    "conversation:read": "查看会话",
    "conversation:write": "管理会话与发言",
    "memory:read": "查看记忆",
    "memory:write": "确认与修正记忆",
    "memory:delete": "遗忘与清空记忆",
    "memory:preferences": "管理长期记忆开关",
    "credential:write": "管理连接凭据",
    "credential:use": "使用连接凭据",
    "content:derive": "生成派生内容",
    "content:delete": "删除内容",
    "content:cleanup": "清理已删除内容",
    "artifact:cleanup": "清理文件",
    "recovery:initialize": "初始化内容范围",
    "recovery:block": "封锁恢复范围",
    "recovery:complete": "完成恢复核验",
    "release:publish": "正式发布",
    "data:export": "导出数据",
    "data:read_sensitive": "读取敏感原文",
}
IndependentAction = Literal["release:publish", "data:export", "data:read_sensitive", "run:approve"]
INDEPENDENT_ACTIONS: frozenset[str] = frozenset(get_args(IndependentAction))
PLATFORM_ACTIONS = frozenset(
    {
        "account:manage",
        "role:grant",
        "menu:manage",
        "channel:create",
        "channel:govern",
        "audit:read",
        "usage:platform",
    }
)
BUILDER_ACTIONS = frozenset(
    {
        "model:manage",
        "agent:manage",
        "prompt:manage",
        "tool:manage",
        "integration:manage",
        "mcp:manage",
        "skill:manage",
        "version:edit",
        "version:freeze",
        "version:read",
        "run:create",
        "run:read",
        "run:content",
        "evaluation:manage",
        "evaluation:read",
        "evaluation:content",
        "artifact:upload",
        "artifact:download",
        "snapshot:read",
        "credential:write",
        "credential:use",
        "content:derive",
        "conversation:read",
        "conversation:write",
    }
)
ROLE_NAMES = {
    "platform_admin": "平台管理员",
    "channel_admin": "渠道管理员",
    "builder": "开发配置人员",
    "operator": "运营人员",
    "analyst": "分析人员",
    "auditor": "审计人员",
}
ROLE_ACTIONS = {
    "platform_admin": PLATFORM_ACTIONS,
    "channel_admin": frozenset(ACTION_NAMES)
    - INDEPENDENT_ACTIONS
    - (PLATFORM_ACTIONS - {"audit:read"}),
    "builder": BUILDER_ACTIONS,
    "operator": frozenset(
        {
            "run:create",
            "run:read",
            "run:content",
            "feedback:manage",
            "evaluation:read",
            "evaluation:content",
            "evaluation:review",
            "conversation:read",
            "conversation:write",
            "memory:read",
            "memory:write",
            "memory:preferences",
            "artifact:download",
        }
    ),
    "analyst": frozenset(
        {"metric:read", "analysis:run", "report:read", "usage:read", "artifact:download"}
    ),
    "auditor": frozenset(
        {"version:read", "run:read", "usage:read", "audit:read", "evaluation:read"}
    ),
}
GOVERNANCE_ACTIONS = frozenset(
    {
        "membership:read",
        "membership:manage",
        "grant:read",
        "grant:manage",
        "channel:manage",
        "environment:manage",
        "client:manage",
        "key:manage",
        "data_scope:manage",
    }
)


def role_actions(roles: list[str]) -> frozenset[str]:
    return frozenset().union(*(ROLE_ACTIONS.get(role, frozenset()) for role in roles))


# 审计动作同时用于平台和渠道，不能作为平台独占动作排除。
PLATFORM_ONLY_ACTIONS = PLATFORM_ACTIONS - {"audit:read"}
