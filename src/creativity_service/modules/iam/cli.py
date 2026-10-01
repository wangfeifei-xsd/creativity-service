"""部署初始化及撤销补偿命令，密码不进入参数或日志。"""

import argparse
import asyncio
import getpass

from pydantic import SecretStr

from creativity_service.core.config import Settings
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.schemas import AccountCreate
from creativity_service.modules.iam.services import build_iam_services


async def execute(args: argparse.Namespace) -> None:
    settings = Settings()
    infrastructure = Infrastructure(settings)
    iam = build_iam_services(
        infrastructure.engine,
        infrastructure.redis_clients["redis_auth"],
        settings.redis_key_prefix,
        management_ttl=settings.management_token_ttl,
        service_ttl=settings.service_token_ttl,
    )
    try:
        if args.command == "init-admin":
            password = getpass.getpass("初始密码：")
            if password != getpass.getpass("再次输入初始密码："):
                raise ServiceError("PASSWORD_MISMATCH", "两次密码不一致", 422)
            account = await iam.accounts.initialize_admin(
                AccountCreate(
                    login_name=args.login_name,
                    display_name=args.display_name,
                    initial_password=SecretStr(password),
                )
            )
            print(f"管理员 {account.display_name} 已创建，首次登录须修改密码。")
        else:
            completed, pending = await iam.revocations.reconcile(args.limit)
            print(f"已完成 {completed} 条撤销补偿，仍有 {pending} 条暂不可处理。")
            if pending:
                raise SystemExit(1)
    finally:
        await infrastructure.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="账号部署与维护命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("init-admin", help="创建唯一的初始管理员")
    initialize.add_argument("--login-name", required=True)
    initialize.add_argument("--display-name", required=True)
    reconcile = subparsers.add_parser("reconcile-revocations", help="重试认证撤销清理")
    reconcile.add_argument("--limit", type=int, default=100)
    try:
        asyncio.run(execute(parser.parse_args()))
    except ServiceError as exc:
        raise SystemExit(exc.message) from None


if __name__ == "__main__":
    main()
