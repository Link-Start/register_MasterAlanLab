from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent


@dataclass(slots=True)
class Settings:
    base_dir: Path = HERE
    proxy: str = ""
    timeout: float = 30
    impersonate: str = "chrome146"
    language: str = "schinese"
    country: str = ""
    poll_interval: float = 3
    verify_timeout: float = 60
    captcha_attempts: int = 2
    batch_delay: float = 5
    captcha_key: str = ""
    captcha_base_url: str = "https://api.captcha.run/v2/tasks"
    captcha_poll_interval: float = 3
    captcha_max_wait: float = 120
    mail_provider: str = "auto"
    mail_file: str = ""
    mail_max_wait: float = 300
    mail_poll_interval: float = 5
    mail_request_timeout: float = 30
    mail_scan_limit: int = 20
    corouter_base_url: str = "https://mail.corouter.cc"
    corouter_tenant_id: str = ""
    corouter_api_key: str = field(default="", repr=False)
    corouter_group_id: str = ""
    corouter_request_timeout: float = 90
    corouter_poll_interval: float = 5
    imap_host: str = "outlook.office365.com"
    imap_port: int = 993
    imap_folders: tuple[str, ...] = ("INBOX", "Junk", "Junk Email", "Spam")
    pop3_host: str = "outlook.office365.com"
    pop3_port: int = 995
    account_prefix: str = ""
    account_name_attempts: int = 8
    password: str = ""
    output: str = "accounts.csv"
    text_output: str = ""
    failed_output: str = "failed.jsonl"
    attempts_output: str = "attempts.jsonl"

    def path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.base_dir / path


def load_config(path: str | Path | None = None) -> Settings:
    config_path = Path(path).expanduser().resolve() if path else HERE / "config.yaml"
    if not config_path.exists():
        if path is not None:
            raise FileNotFoundError(f"配置文件不存在：{config_path}")
        return Settings()
    values = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(values, dict):
        raise ValueError("config.yaml 必须是 YAML 对象")
    config = Settings(base_dir=config_path.parent)
    mapping = {
        "STEAM_PROXY": "proxy",
        "STEAM_TIMEOUT": "timeout",
        "STEAM_IMPERSONATE": "impersonate",
        "STEAM_LANGUAGE": "language",
        "STEAM_COUNTRY": "country",
        "STEAM_POLL_INTERVAL": "poll_interval",
        "STEAM_VERIFY_TIMEOUT": "verify_timeout",
        "STEAM_CAPTCHA_ATTEMPTS": "captcha_attempts",
        "BATCH_DELAY": "batch_delay",
        "CAPTCHA_RUN_KEY": "captcha_key",
        "CAPTCHA_RUN_BASE_URL": "captcha_base_url",
        "CAPTCHA_RUN_POLL_INTERVAL": "captcha_poll_interval",
        "CAPTCHA_RUN_MAX_WAIT": "captcha_max_wait",
        "MAIL_PROVIDER": "mail_provider",
        "MAIL_FILE": "mail_file",
        "MAIL_MAX_WAIT": "mail_max_wait",
        "MAIL_POLL_INTERVAL": "mail_poll_interval",
        "MAIL_REQUEST_TIMEOUT": "mail_request_timeout",
        "MAIL_SCAN_LIMIT": "mail_scan_limit",
        "COROUTER_MAIL_BASE_URL": "corouter_base_url",
        "COROUTER_MAIL_TENANT_ID": "corouter_tenant_id",
        "COROUTER_MAIL_API_KEY": "corouter_api_key",
        "COROUTER_MAIL_GROUP_ID": "corouter_group_id",
        "COROUTER_MAIL_REQUEST_TIMEOUT": "corouter_request_timeout",
        "COROUTER_MAIL_POLL_INTERVAL": "corouter_poll_interval",
        "IMAP_HOST": "imap_host",
        "IMAP_PORT": "imap_port",
        "IMAP_FOLDERS": "imap_folders",
        "POP3_HOST": "pop3_host",
        "POP3_PORT": "pop3_port",
        "ACCOUNT_PREFIX": "account_prefix",
        "ACCOUNT_NAME_ATTEMPTS": "account_name_attempts",
        "STEAM_PASSWORD": "password",
        "STEAM_OUTPUT": "output",
        "STEAM_TEXT_OUTPUT": "text_output",
        "STEAM_FAILED_OUTPUT": "failed_output",
        "STEAM_ATTEMPTS_OUTPUT": "attempts_output",
    }
    unknown = set(values) - set(mapping)
    if unknown:
        raise ValueError(f"不支持的配置项：{', '.join(sorted(map(str, unknown)))}")
    for key, attribute in mapping.items():
        if key not in values or values[key] is None:
            continue
        current = getattr(config, attribute)
        value: Any = values[key]
        if attribute == "imap_folders":
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(folder, str) and folder for folder in value)
            ):
                raise ValueError("IMAP_FOLDERS 必须是非空的文件夹名称列表")
            value = tuple(value)
        elif isinstance(current, (int, float)):
            if isinstance(value, bool):
                raise ValueError(f"{key} 必须是数值")
            number = float(value)
            if not math.isfinite(number) or number < (0 if attribute == "batch_delay" else 0.001):
                raise ValueError(f"{key} 必须是有限的正数（BATCH_DELAY 可为 0）")
            if isinstance(current, int) and attribute not in {
                "timeout",
                "poll_interval",
                "verify_timeout",
                "batch_delay",
                "captcha_poll_interval",
                "captcha_max_wait",
                "mail_max_wait",
                "mail_poll_interval",
                "mail_request_timeout",
                "corouter_request_timeout",
                "corouter_poll_interval",
            }:
                if not number.is_integer():
                    raise ValueError(f"{key} 必须是整数")
                value = int(number)
            else:
                value = number
        else:
            value = str(value) if attribute == "password" else str(value).strip()
        setattr(config, attribute, value)
    if config.mail_provider not in {"auto", "manual", "graph", "imap", "pop3", "corouter"}:
        raise ValueError("MAIL_PROVIDER 可选值：auto、manual、graph、imap、pop3、corouter")
    if not config.output:
        raise ValueError("STEAM_OUTPUT 不能为空")
    if not config.attempts_output:
        raise ValueError("STEAM_ATTEMPTS_OUTPUT 不能为空，创建前需要保存可恢复的账户凭据")
    paths = [
        config.path(value).resolve()
        for value in (
            config.output,
            config.text_output,
            config.failed_output,
            config.attempts_output,
        )
        if value
    ]
    if len(set(paths)) != len(paths):
        raise ValueError("账号明细、TXT、失败和尝试记录必须使用不同文件")
    return config
