"""MCP 服务间鉴权配置；应用密钥继续使用现有凭据密文存储。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0044_mcp_client_credentials"
down_revision = "0043_resource_management"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "mcp_connections",
        sa.Column(
            "authentication",
            JSONB(),
            nullable=True,
            comment="鉴权方式、令牌地址与应用标识；密钥单独加密保存",
        ),
    )


def downgrade():
    raise RuntimeError("服务间鉴权配置不能通过降级自动丢弃")
