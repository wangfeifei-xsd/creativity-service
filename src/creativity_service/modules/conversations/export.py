"""导出会话交接契约，正式 HTTP 契约同时归入统一 OpenAPI。"""

import argparse
import json
from pathlib import Path

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
    root = Path("contracts/conversations")
    for model in (
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
    ):
        path = root / f"{model.__name__}.schema.json"
        content = (
            json.dumps(model.model_json_schema(mode="serialization"), ensure_ascii=False, indent=2)
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"会话契约过期：{path.name}")
        else:
            root.mkdir(exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
