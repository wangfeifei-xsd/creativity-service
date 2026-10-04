"""受理配额按状态和周期读取的普通索引，供模型和本版本迁移共同使用。"""

INDEXES = (
    ("ix_platform_limits_current", "platform_limits", ("channel_id", "limit_code", "created_at")),
    (
        "ix_platform_quota_held",
        "platform_quota_occupancies",
        ("channel_id", "limit_code", "status"),
    ),
    (
        "ix_platform_quota_period",
        "platform_quota_occupancies",
        ("channel_id", "limit_code", "created_at"),
    ),
    ("ix_admissions_active", "admissions", ("channel_id", "status", "created_at")),
)
