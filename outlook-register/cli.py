from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from config import Settings
from models import RegistrationInput
from output import append_success_txt
from signup import OutlookSignupProtocol


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconstructed Microsoft signup protocol")
    parser.add_argument("--member-name", required=True, help="Microsoft sign-in name, for example name@outlook.com")
    parser.add_argument("--password", required=True)
    parser.add_argument("--first-name", default="Alan")
    parser.add_argument("--last-name", default="User")
    parser.add_argument("--country", default=None)
    parser.add_argument("--market", default=None, help="Signup market, for example ja-JP")
    parser.add_argument("--birth-date", help="YYYY-MM-DD")
    parser.add_argument("--proxy", default=None, help="http://host:port or user:pass@host:port")
    parser.add_argument("--captcha-token", default=None, help="Already solved PxCaptcha2 token")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--execute", action="store_true", help="send risk/create requests; default only prepares protocol")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--output", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env(args.env_file)
    if args.proxy:
        settings.proxy = args.proxy
    if args.market:
        settings.market = args.market
    if args.output:
        settings.output_path = settings.output_path.with_name(args.output)

    birth_date = date.fromisoformat(args.birth_date) if args.birth_date else None
    registration = RegistrationInput(
        member_name=args.member_name,
        password=args.password,
        first_name=args.first_name,
        last_name=args.last_name,
        country=args.country or settings.country,
        birth_date=birth_date,
        proxy=args.proxy or settings.proxy,
        captcha_token=args.captcha_token,
    )
    protocol = OutlookSignupProtocol(settings)
    try:
        result = protocol.register(registration, execute=args.execute)
    except Exception as exc:  # noqa: BLE001 - CLI converts protocol errors to JSON
        result = {"success": False, "member_name": registration.member_name, "error": str(exc), "trace": protocol.trace}
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"失败: {exc}", file=sys.stderr)
        return 1

    if hasattr(result, "as_dict"):
        value = result.as_dict()
        append_success_txt(settings.output_path, result)
    else:
        value = result
    if args.as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value.get("message", ""))
        print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
