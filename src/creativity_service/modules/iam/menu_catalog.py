"""页面注册白名单只约束可绑定组件，菜单组织和角色选择由数据库保存。"""

PAGES = {
    "model-providers": ("模型供应商", "platform", ("channel:govern",)),
    "models": ("模型配置", "channel", ("model:manage",)),
    "model-routes": ("模型路由", "channel", ("model:manage",)),
    "agents": ("智能体", "channel", ("agent:manage",)),
    "prompts": ("提示词", "channel", ("prompt:manage", "version:read")),
    "tools": ("工具", "channel", ("tool:manage",)),
    "skills": ("技能管理", "channel", ("skill:manage",)),
    "mcp-connections": ("MCP 连接", "channel", ("mcp:manage",)),
    "runs": ("运行记录", "channel", ("run:read",)),
    "conversations": ("会话管理", "channel", ("conversation:read",)),
    "memories": ("记忆管理", "channel", ("memory:read",)),
    "evaluations": ("效果评测", "channel", ("evaluation:read",)),
    "integrations": ("业务接入", "channel", ("integration:manage",)),
    "usage": ("用量", "channel", ("usage:read",)),
    "concurrency-limits": ("并发限额", "channel", ("budget:manage",)),
    "channels": ("渠道管理", "both", ("channel:govern", "channel:manage")),
    "accounts": ("账号管理", "platform", ("account:manage",)),
    "roles": ("角色管理", "both", ("role:grant", "membership:manage")),
    "menus": ("菜单管理", "platform", ("menu:manage",)),
    "members": ("成员与权限", "channel", ("membership:read",)),
    "resource-grants": ("资源授权", "channel", ("grant:read",)),
    "platform-limits": ("平台限额", "platform", ("channel:govern",)),
    "platform-usage": ("平台用量", "platform", ("usage:platform",)),
    "audit-events": ("操作审计", "both", ("audit:read",)),
}

PROTECTED_PAGES = frozenset({"accounts", "roles", "menus"})
