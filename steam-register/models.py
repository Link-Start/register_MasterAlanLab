from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class RegistrationError(RuntimeError):
    def __init__(self, stage: str, message: str, code: str | int | None = None):
        super().__init__(message)
        self.stage = stage
        self.code = code


@dataclass(slots=True)
class MailboxRecord:
    email: str
    password: str = field(default="", repr=False)
    client_id: str = ""
    refresh_token: str = field(default="", repr=False)
    account_id: str = ""

    def __post_init__(self) -> None:
        self.email = self.email.strip()
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", self.email):
            raise ValueError("邮箱地址格式不正确")

    @property
    def has_oauth(self) -> bool:
        return bool(self.client_id and self.refresh_token)


@dataclass(slots=True)
class RegistrationInput:
    email: str
    account_name: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        MailboxRecord(self.email)
        if not re.fullmatch(r"[a-zA-Z0-9_]{3,64}", self.account_name):
            raise ValueError("Steam 账户名须为 3–64 位字母、数字或下划线")
        if not 8 <= len(self.password) <= 64 or not all(
            32 <= ord(char) < 127 for char in self.password
        ):
            raise ValueError("Steam 密码须为 8–64 位可打印 ASCII 字符")
        if self.account_name.casefold() == self.password.casefold():
            raise ValueError("Steam 密码不能与账户名相同")


@dataclass(slots=True)
class SignupPageContext:
    init_id: str
    lt: str = "0"
    embedded_appid: int = 0
    guest: bool = False


@dataclass(slots=True)
class CaptchaChallenge:
    gid: str
    kind: int = 3
    sitekey: str = ""
    s: str = ""


@dataclass(slots=True)
class RegistrationResult:
    success: bool
    email: str
    account_name: str
    password: str = field(repr=False)
    stage: str = ""
    code: str | int | None = None
    message: str = ""
    steamid: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        # 密码、验证码 token、邮箱 OAuth 凭据和 Cookie 仅保存在必要的本地结果中。
        return {
            "success": self.success,
            "email": self.email,
            "account_name": self.account_name,
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "steamid": self.steamid,
            "trace": self.trace,
        }
