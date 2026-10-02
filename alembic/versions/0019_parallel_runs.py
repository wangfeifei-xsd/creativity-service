"""汇合任务运行与同期业务接入迁移分支，不改写已登记模块修订。"""

revision = "0019_parallel_runs"
down_revision = ("0011_runs", "0018_integrations")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
