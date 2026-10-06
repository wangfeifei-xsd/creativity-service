"""数据域类型的中文展示与填写建议，不限制接入系统的自定义类型。"""

DATA_SCOPE_TYPE_NAMES = {
    "workspace": "工作区",
    "organization": "组织",
    "org": "组织",
    "department": "部门",
    "project": "项目",
    "default": "默认业务域",
    "club": "俱乐部",
}

# 建议值只帮助首次填写；实际映射仍由用户按源系统协议明确选择或输入。
SUGGESTED_DATA_SCOPE_TYPES = ("workspace", "organization", "department", "project", "default")
