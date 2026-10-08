"""导出会话交接契约，正式 HTTP 契约同时归入统一 OpenAPI。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.modules.conversations.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationRunRequest,
    DeletionImpact,
    DeletionView,
    MessageInput,
    MessagePage,
    MessageReceipt,
    SelectedContext,
    SummaryView,
    TitleInput,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出会话契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/conversations.json"),
        schema_bundle(
            (
                ConversationCreate,
                ConversationDetail,
                ConversationList,
                ConversationRunRequest,
                DeletionImpact,
                DeletionView,
                MessageInput,
                MessagePage,
                MessageReceipt,
                SelectedContext,
                SummaryView,
                TitleInput,
            ),
            mode="serialization",
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
