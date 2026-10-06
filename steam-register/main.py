from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import ExitStack

import yaml

from captcha import CaptchaRunClient, ManualCaptchaSolver
from config import load_config
from corouter_mail_provider import load_corouter_mailboxes
from http_client import SteamSession
from mail_provider import create_mail_provider, load_mailboxes
from models import MailboxRecord, RegistrationError, RegistrationInput
from output import registered_emails, save_account, save_attempt, save_failure
from signup import SteamRegistrar
from utils import random_account_name, random_password


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Steam 注册协议客户端")
    result.add_argument("--config", help="YAML 配置文件；默认 steam-register/config.yaml")
    source = result.add_mutually_exclusive_group()
    source.add_argument("--email", help="单个邮箱地址；corouter 模式需为租户中的邮箱")
    source.add_argument("--mail-file", help="导入邮箱文件（路径相对于配置文件目录）")
    result.add_argument(
        "--mail-provider", choices=("auto", "manual", "graph", "imap", "pop3", "corouter")
    )
    result.add_argument("--mail-group", help="Corouter 邮箱分组 ID 或名称")
    result.add_argument("--account-name", help="单次注册的 Steam 登录账户名；默认拼音加四位数字")
    result.add_argument("--password", help="Steam 密码；默认配置值或随机生成")
    result.add_argument("--captcha-token", help="手动提供当前 sitekey 的有效 token，仅使用一次")
    result.add_argument("--verification-url", help="当前创建会话的邮件验证链接")
    result.add_argument("--proxy", help="Steam 请求代理；邮箱和打码服务仍直连")
    result.add_argument("-n", "--count", type=int, default=1, help="最多处理几个邮箱，默认 1")
    result.add_argument("--all", action="store_true", help="处理租户或文件中全部未成功注册的邮箱")
    result.add_argument("--output", help="CSV 账号明细路径")
    result.add_argument(
        "--probe", action="store_true", help="只获取注册页和验证码，不打码或创建账户"
    )
    result.add_argument("--json", action="store_true", help="输出脱敏 JSON 结果")
    return result


def main(argv: list[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    try:
        settings = load_config(args.config)
        if args.proxy is not None:
            settings.proxy = args.proxy
        if args.output is not None:
            settings.output = args.output
        if args.mail_provider:
            settings.mail_provider = args.mail_provider
        if args.mail_group is not None:
            settings.corouter_group_id = args.mail_group.strip()
        if args.count <= 0:
            raise ValueError("--count 必须大于 0")
        if args.probe:
            with SteamSession(settings) as client:
                data = SteamRegistrar(client, settings).probe()
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        mail_file = args.mail_file or (settings.mail_file if not args.email else "")
        if args.email:
            records = [MailboxRecord(args.email)]
        elif mail_file:
            records = load_mailboxes(settings.path(mail_file))
        elif settings.mail_provider == "corouter":
            records = None
        else:
            argument_parser.error("请指定 --email 或 --mail-file；只检查接口请使用 --probe")
        output = settings.path(settings.output)
        output_paths = [
            settings.path(value).resolve()
            for value in (
                settings.output,
                settings.text_output,
                settings.failed_output,
                settings.attempts_output,
            )
            if value
        ]
        if len(set(output_paths)) != len(output_paths):
            raise ValueError("账号明细、TXT、失败和尝试记录必须使用不同文件")
        existing = registered_emails(output)
        if settings.mail_provider == "corouter":
            if records is not None:
                records = [record for record in records if record.email.casefold() not in existing]
                if not args.all:
                    records = records[: args.count]
            records = load_corouter_mailboxes(settings, records)
        records = [record for record in records if record.email.casefold() not in existing]
        if not args.all:
            records = records[: args.count]
        if len(records) > 1 and (args.account_name or args.captcha_token or args.verification_url):
            raise ValueError("固定账户名、验证码 token 和验证链接仅适用于单次注册")
        failed = 0
        for index, record in enumerate(records):
            fields = RegistrationInput(
                record.email,
                args.account_name or random_account_name(settings.account_prefix),
                args.password or settings.password or random_password(),
            )
            if settings.text_output and any(
                "----" in value for value in (fields.account_name, fields.password, fields.email)
            ):
                raise ValueError("TXT 输出字段包含分隔符 ----，请清空 STEAM_TEXT_OUTPUT 使用 CSV")
            # 先校验邮箱配置，再向 Steam 或打码服务发起请求。
            with ExitStack() as stack:
                mailbox = create_mail_provider(record, settings, link=args.verification_url or "")
                stack.callback(mailbox.close)
                solver = (
                    ManualCaptchaSolver(args.captcha_token or "")
                    if (args.captcha_token or not settings.captcha_key)
                    else CaptchaRunClient(settings)
                )
                stack.callback(solver.close)
                save_attempt(fields, settings.path(settings.attempts_output))
                client = stack.enter_context(SteamSession(settings))
                result = SteamRegistrar(
                    client,
                    settings,
                    mailbox,
                    solver,
                    account_name_factory=(
                        None
                        if args.account_name
                        else lambda: random_account_name(settings.account_prefix)
                    ),
                    on_account_name_change=lambda updated: save_attempt(
                        updated, settings.path(settings.attempts_output)
                    ),
                ).register(fields)
            if result.success:
                try:
                    save_account(
                        result,
                        output,
                        settings.path(settings.text_output) if settings.text_output else None,
                    )
                except (OSError, ValueError) as error:
                    print(
                        f"账户 {fields.account_name} 已创建，但保存结果失败"
                        f"（{type(error).__name__}）；"
                        f"请检查 {output}，凭据保存在 {settings.path(settings.attempts_output)}；"
                        "本批次已停止",
                        file=sys.stderr,
                    )
                    return 2
            else:
                failed += 1
                if settings.failed_output:
                    save_failure(result, settings.path(settings.failed_output))
            if args.json:
                print(json.dumps(result.as_dict(), ensure_ascii=False))
            else:
                print(
                    f"[{index + 1}/{len(records)}] {record.email} / {fields.account_name}："
                    f"{result.message}"
                    + (f"（{result.stage}, {result.code}）" if not result.success else "")
                )
            if result.code in {"RATE_LIMITED", "OUTCOME_UNKNOWN"} or (
                settings.mail_provider == "corouter"
                and result.code in {"MAIL_AUTH_FAILED", "MAIL_ACCESS_DENIED", "MAIL_QUOTA_EXCEEDED"}
            ):
                break
            if index + 1 < len(records):
                time.sleep(settings.batch_delay)
        if not records:
            print("没有待处理邮箱，已成功注册的邮箱均已跳过")
        return 1 if failed else 0
    except (OSError, ValueError, RegistrationError, yaml.YAMLError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已停止；如果在创建请求期间中断，请先检查账户状态再重试", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
