from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

DEFAULT_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"


@dataclass(slots=True)
class RegistrationInput:
    """Fields used by the original oureg.CreateAccount pipeline."""

    member_name: str
    password: str
    first_name: str = "Alan"
    last_name: str = "User"
    country: str = "US"
    birth_date: date | None = None
    proxy: str | None = None
    captcha_token: str | None = None
    captcha_type: str = "HumanCaptcha"

    def __post_init__(self) -> None:
        if "@" not in self.member_name:
            raise ValueError("member_name must be an email-style Microsoft sign-in name")
        if len(self.password) < 8:
            raise ValueError("password must contain at least 8 characters")


@dataclass(slots=True)
class SignupPageContext:
    uaid: str
    api_canary: str = ""
    unauth_session_id: str = ""
    url_dfp: str = ""
    hip_fid: str = ""
    hpgid: str = ""
    scenario_id: str = ""
    ui_flavor: str = ""
    pref_sms_country: str = ""
    continuation_token: str = ""
    txn_id: str = ""
    rid: str = ""
    ticks: str = ""
    auth_key: str = ""
    cid: str = ""
    arkose_blob: str = ""
    verification_code_slt: str = ""
    private_access_token: str = ""
    wreply: str = ""
    risk_required: bool = False
    raw_html: str = ""


@dataclass(slots=True)
class RegistrationResult:
    success: bool
    member_name: str
    password: str = field(default="", repr=False)
    client_id: str = DEFAULT_CLIENT_ID
    refresh_token: str = field(default="", repr=False)
    access_token: str = field(default="", repr=False)
    status_code: int | None = None
    code: str | int | None = None
    redirect_url: str | None = None
    message: str = ""
    dry_run: bool = False
    response: dict[str, Any] = field(default_factory=dict)

    @property
    def token(self) -> str:
        return self.refresh_token or self.access_token

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "member_name": self.member_name,
            "password": self.password,
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "token": self.token,
            "status_code": self.status_code,
            "code": self.code,
            "redirect_url": self.redirect_url,
            "message": self.message,
            "dry_run": self.dry_run,
            "response": self.response,
        }


@dataclass(slots=True)
class CaptchaSolution:
    silent_token: str = ""
    press_token: str = ""
    origin: str = ""

    @property
    def token(self) -> str:
        return self.press_token or self.silent_token
