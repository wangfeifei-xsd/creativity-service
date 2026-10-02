"""服务端命令行调用；配置和凭据由执行环境提供。"""

import argparse
import asyncio
import base64
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import httpx

from .client import BusinessBackendClient, PlatformError, Principal, WaitTimeout


class ServerPrincipalFile:
    """联调时读取后端受控身份文件；线上替换为当前会话和权限服务。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def current(self) -> Principal:
        value = json.loads(await asyncio.to_thread(self.path.read_text, encoding="utf-8"))
        return Principal(**value)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Creativity 通用后端调用示例")
    root.add_argument("--principal", required=True, type=Path, help="服务端受控身份 JSON 文件")
    sub = root.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="以固定幂等键创建运行")
    create.add_argument("--agent", required=True)
    create.add_argument("--input", required=True, type=Path)
    create.add_argument("--idempotency-key", required=True)
    create.add_argument("--delivery", choices=["sync", "async", "stream"], default="async")
    create.add_argument("--wait", action="store_true")
    for name in ("query", "wait", "events", "cancel"):
        item = sub.add_parser(name)
        item.add_argument("run_id")
        if name == "events":
            item.add_argument("--after", type=int, default=0)
    download = sub.add_parser("download")
    download.add_argument("artifact_id")
    download.add_argument("--output", type=Path, required=True)
    conversation = sub.add_parser("conversation")
    conversation.add_argument("--agent", required=True)
    conversation.add_argument("--title", required=True)
    message = sub.add_parser("message")
    message.add_argument("conversation_id")
    message.add_argument("--message-id", required=True)
    message.add_argument("--content", required=True)
    message.add_argument("--input", type=Path, required=True)
    return root


def show(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


async def main(args: argparse.Namespace) -> None:
    async with BusinessBackendClient(
        base_url=os.environ["CREATIVITY_API_URL"],
        api_key=os.environ["CREATIVITY_API_KEY"],
        kid=os.environ["CREATIVITY_DELEGATION_KID"],
        signing_secret=base64.b64decode(os.environ["CREATIVITY_DELEGATION_SECRET"], validate=True),
        issuer=os.environ["CREATIVITY_DELEGATION_ISSUER"],
        audience=os.environ["CREATIVITY_DELEGATION_AUDIENCE"],
        principals=ServerPrincipalFile(args.principal),
    ) as client:
        if args.command == "create":
            receipt = await client.submit(
                args.agent, json.loads(args.input.read_text()), args.idempotency_key, args.delivery
            )
            show(receipt)
            if args.wait:
                show(await client.wait(receipt["run_id"]))
        elif args.command == "events":
            async for event in client.subscribe(args.run_id, args.after):
                show(asdict(event))
        elif args.command == "download":
            content = await client.download(args.artifact_id)
            args.output.write_bytes(content)
            show({"path": str(args.output), "size_bytes": len(content)})
        elif args.command == "conversation":
            show(await client.create_conversation(args.agent, args.title))
        elif args.command == "message":
            show(
                await client.send_message(
                    args.conversation_id,
                    args.message_id,
                    args.content,
                    json.loads(args.input.read_text()),
                )
            )
        else:
            show(await getattr(client, args.command)(args.run_id))


if __name__ == "__main__":
    args = parser().parse_args()
    try:
        asyncio.run(main(args))
    except (PlatformError, WaitTimeout) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (KeyError, ValueError, OSError, httpx.HTTPError):
        print("配置、文件或网络错误；检查服务端配置后使用原幂等键或运行标识继续。", file=sys.stderr)
        raise SystemExit(2) from None
