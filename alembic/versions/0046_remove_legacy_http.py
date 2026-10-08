"""移除固定业务 HTTP 连接、契约测试与专用密文，独立委托继续保留。"""

from alembic import op

revision = "0046_remove_legacy_http"
down_revision = "0045_mcp_plain_credentials"
branch_labels = None
depends_on = None


def upgrade():
    # 此用途仅由已移除的业务接入接口写入；一并清除未绑定连接的遗留密文。
    # 模型、MCP、委托和 Webhook 共用凭据表，不能按主密钥版本清理。
    op.execute("DELETE FROM credentials WHERE purpose = 'http_tool'")
    op.drop_table("integration_tests")
    op.drop_table("integrations")


def downgrade():
    raise RuntimeError("旧业务 HTTP 配置与凭据已移除，回退须恢复升级前备份")
