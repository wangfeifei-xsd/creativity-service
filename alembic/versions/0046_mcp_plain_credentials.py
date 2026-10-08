"""MCP 凭据直接存储；在线升级一次性转换旧密文，运行时不再依赖主密钥。"""

import base64
import json

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from alembic import context, op

revision = "0046_mcp_plain_credentials"
down_revision = "0045_remove_legacy_http"
branch_labels = None
depends_on = None


class LegacyKeys(BaseSettings):
    """只在转换旧密文时读取历史配置，新库和后续运行不需要此配置。"""

    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_MCP_",
        env_file=(".env", ".env.local"),
        extra="ignore",
        hide_input_in_errors=True,
    )
    encryption_keys: dict[str, SecretStr] = {}


def upgrade():
    op.add_column(
        "credentials",
        sa.Column("secret_value", sa.Text(), nullable=True, comment="MCP 凭据原文"),
    )
    op.alter_column(
        "mcp_connections", "authentication", comment="鉴权方式、令牌地址与应用标识；凭据单独保存"
    )
    op.alter_column("mcp_oauth_flows", "verifier_ref", comment="PKCE 验证凭据引用")
    op.alter_column("mcp_oauth_tokens", "credential_ref", comment="委托令牌凭据引用")
    if context.is_offline_mode():
        # 离线建库没有历史记录；已有密文的数据库须在线升级才能转换。
        return
    connection = op.get_bind()
    credentials = sa.table(
        "credentials",
        *(
            sa.column(name)
            for name in (
                "id",
                "channel_id",
                "environment",
                "purpose",
                "ciphertext",
                "key_version",
                "secret_value",
            )
        ),
    )
    legacy_keys = None
    while (
        rows := connection.execute(
            sa.select(credentials)
            .where(credentials.c.purpose == "mcp", credentials.c.ciphertext.is_not(None))
            .order_by(credentials.c.channel_id, credentials.c.id)
            .limit(500)
            .with_for_update()
        )
        .mappings()
        .all()
    ):
        if legacy_keys is None:
            legacy_keys = LegacyKeys().encryption_keys
        for row in rows:
            try:
                master = base64.b64decode(
                    legacy_keys[row["key_version"]].get_secret_value(), validate=True
                )
                aad = json.dumps(
                    [row["channel_id"], row["environment"], row["id"], "mcp"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                value = bytes(row["ciphertext"])
                plaintext = AESGCM(master).decrypt(value[:12], value[12:], aad).decode("utf-8")
                if not plaintext:
                    raise ValueError("旧凭据为空")
            except Exception:
                raise RuntimeError(
                    "旧 MCP 凭据转换失败；请临时提供原 CREATIVITY_MCP_ENCRYPTION_KEYS，"
                    "完成本次升级后移除。迁移已回滚，原凭据保留。"
                ) from None
            connection.execute(
                credentials.update()
                .where(
                    credentials.c.channel_id == row["channel_id"],
                    credentials.c.environment == row["environment"],
                    credentials.c.id == row["id"],
                )
                .values(secret_value=plaintext, ciphertext=None, key_version=None)
            )


def downgrade():
    if context.is_offline_mode() or op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM credentials WHERE secret_value IS NOT NULL)")
    ):
        raise RuntimeError("已有 MCP 原文凭据不能通过降级丢弃，请恢复升级前备份")
    op.drop_column("credentials", "secret_value")
    op.alter_column("mcp_oauth_flows", "verifier_ref", comment="加密验证凭据引用")
    op.alter_column("mcp_oauth_tokens", "credential_ref", comment="加密委托令牌引用")
    op.alter_column(
        "mcp_connections",
        "authentication",
        comment="鉴权方式、令牌地址与应用标识；密钥单独加密保存",
    )
