"""部署交接命令：独立清单备份、恢复前封锁、清理重放和完成证明。"""

import argparse
import asyncio
import json
from pathlib import Path

from creativity_service.core.deletion.ledger import DeletionLedger
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.data_lifecycle.recovery import (
    backup_manifest,
    block_restore,
    replay_restore,
)
from creativity_service.modules.data_lifecycle.retention import scan_retention
from creativity_service.workers.cleanup import runtime


async def execute(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "restore-block":
        return await block_restore(args.channel)
    if args.command == "restore-check":
        value = await DeletionLedger().operate(args.channel, required=True)
        if not value["blocked"] or value["sequence"] < args.minimum_sequence:
            raise ServiceError("RECOVERY_PROOF_INVALID", "入口未关闭或删除清单水位不足", 503)
        return {"channel_id": args.channel, "blocked": True, "sequence": value["sequence"]}
    async with runtime() as (service, channels, lifecycle):
        if args.channel not in channels:
            raise ServiceError("NOT_FOUND", "渠道不在服务端目录中", 404)
        if args.command == "backup":
            return await backup_manifest(service, args.channel)
        if args.command == "restore-replay":
            return await replay_restore(service, args.channel, args.minimum_sequence)
        events = await lifecycle.pending(args.channel, "retention")
        retention = await scan_retention(service, args.channel)
        count = await service.sweep(args.channel)
        for event in events:
            await lifecycle.acknowledge(args.channel, event.event_id, "retention")
        return {"channel_id": args.channel, "processed": count, **retention}


def main() -> None:
    parser = argparse.ArgumentParser(description="删除传播与恢复核对")
    parser.add_argument(
        "command", choices=["sweep", "backup", "restore-block", "restore-check", "restore-replay"]
    )
    parser.add_argument("--channel", required=True)
    parser.add_argument("--minimum-sequence", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command in {"restore-check", "restore-replay"} and (
        args.minimum_sequence is None or args.minimum_sequence < 0
    ):
        parser.error("恢复核对必须提供最新备份记录的非负 --minimum-sequence")
    try:
        value = asyncio.run(execute(args))
    except ServiceError as exc:
        raise SystemExit(exc.message) from None
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
