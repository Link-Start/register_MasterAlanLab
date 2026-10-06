from __future__ import annotations

import re
import time
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from config import Settings
from http_client import SteamSession
from models import (
    CaptchaChallenge,
    RegistrationError,
    RegistrationInput,
    RegistrationResult,
    SignupPageContext,
)
from utils import is_steam_url, verification_url

EMAIL_ERRORS = {
    8: "账户字段不符合 Steam 要求",
    13: "邮箱地址不正确",
    14: "账户名不可用",
    17: "Steam 不接受该邮箱服务，请换用其他邮箱",
    101: "验证码被拒绝",
}


class _Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attributes = dict(attrs)
        name = attributes.get("id") or attributes.get("name")
        if name:
            self.values[name] = attributes.get("value") or ""


def parse_signup_page(html: str) -> SignupPageContext:
    parser = _Inputs()
    parser.feed(html)
    init_id = parser.values.get("init_id", "")
    if not init_id.isdigit():
        raise RegistrationError(
            "join", "注册页缺少 init_id，请检查网络或页面是否已变更", "PAGE_CHANGED"
        )
    appid = re.search(r"\bg_embeddedAppID\s*=\s*(\d+)", html)
    guest = re.search(r"\bg_bGuest\s*=\s*(true|false)", html)
    return SignupPageContext(
        init_id,
        parser.values.get("lt", "0"),
        int(appid[1]) if appid else 0,
        bool(guest and guest[1] == "true"),
    )


class SteamRegistrar:
    def __init__(
        self,
        client: SteamSession,
        settings: Settings,
        mail_provider: Any = None,
        captcha_solver: Any = None,
        *,
        account_name_factory: Callable[[], str] | None = None,
        on_account_name_change: Callable[[RegistrationInput], None] | None = None,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.client = client
        self.settings = settings
        self.mail_provider = mail_provider
        self.captcha_solver = captcha_solver
        self.account_name_factory = account_name_factory
        self.on_account_name_change = on_account_name_change
        self.clock = clock
        self.sleep = sleep
        self.count = 0

    def _count(self) -> int:
        self.count += 1
        return self.count

    def open_page(self) -> SignupPageContext:
        params = {"l": self.settings.language}
        if self.settings.country:
            params["cc"] = self.settings.country
        response = self.client.request("GET", "/join/", stage="join", params=params)
        return parse_signup_page(response.text)

    def refresh_captcha(self) -> CaptchaChallenge:
        data = self.client.json(
            "/join/refreshcaptcha",
            stage="refreshcaptcha",
            data={"count": self._count(), "hcaptcha": 1},
        )
        gid = data.get("gid")
        if isinstance(gid, bool) or not isinstance(gid, (str, int)) or not str(gid):
            raise RegistrationError("refreshcaptcha", "Steam 未返回验证码 gid", "INVALID_RESPONSE")
        try:
            kind = int(data.get("type", 3))
        except (TypeError, ValueError) as error:
            raise RegistrationError(
                "refreshcaptcha", "验证码类型格式不正确", "INVALID_RESPONSE"
            ) from error
        return CaptchaChallenge(
            str(gid), kind, str(data.get("sitekey") or ""), str(data.get("s") or "")
        )

    def _send_email(self, email: str, page: SignupPageContext) -> str:
        for attempt in range(self.settings.captcha_attempts):
            challenge = self.refresh_captcha()
            token = self.captcha_solver.solve(challenge)
            data = self.client.json(
                "/join/ajaxverifyemail",
                stage="send_email",
                data={
                    "email": email,
                    "captchagid": challenge.gid,
                    "captcha_text": token,
                    "elang": 6,
                    "init_id": page.init_id,
                    "guest": str(page.guest).lower(),
                },
            )
            code = data.get("success")
            if not isinstance(code, int) or isinstance(code, bool):
                raise RegistrationError(
                    "send_email", "发信响应缺少有效 success 返回码", "INVALID_RESPONSE"
                )
            if code == 1:
                creation_id = data.get("sessionid")
                if (
                    isinstance(creation_id, bool)
                    or not isinstance(creation_id, (str, int))
                    or not str(creation_id).isdigit()
                ):
                    raise RegistrationError(
                        "send_email", "Steam 未返回有效的创建会话 sessionid", "INVALID_RESPONSE"
                    )
                return str(creation_id)
            if code == 101 and attempt + 1 < self.settings.captcha_attempts:
                continue
            message = EMAIL_ERRORS.get(code, "Steam 拒绝发送验证邮件，请检查返回码")
            raise RegistrationError("send_email", message, code)
        raise RegistrationError("send_email", "验证码尝试次数已用完", 101)

    def _follow_link(self, link: str, creation_id: str) -> None:
        try:
            url = verification_url(link, creation_id)
        except ValueError as error:
            raise RegistrationError(
                "verify_link", str(error), "INVALID_VERIFICATION_LINK"
            ) from error
        for _ in range(5):
            response = self.client.request("GET", url, stage="verify_link")
            if 200 <= response.status_code < 300:
                return
            location = response.headers.get("Location")
            url = urljoin(url, location or "")
            if not location or not is_steam_url(url):
                raise RegistrationError(
                    "verify_link", "验证链接返回非 Steam 跳转，已停止访问", "INVALID_REDIRECT"
                )
        raise RegistrationError("verify_link", "验证链接跳转次数过多", "INVALID_REDIRECT")

    def _wait_verified(self, creation_id: str) -> None:
        deadline = self.clock() + self.settings.verify_timeout
        while self.clock() < deadline:
            data = self.client.json(
                "/join/ajaxcheckemailverified",
                stage="check_email",
                data={"creationid": creation_id},
                timeout=min(self.settings.timeout, deadline - self.clock()),
            )
            code = data.get("success")
            if not isinstance(code, int) or isinstance(code, bool):
                raise RegistrationError(
                    "check_email", "邮箱确认响应缺少有效 success 返回码", "INVALID_RESPONSE"
                )
            if code == 1:
                return
            if code in {27, 29, 42}:
                raise RegistrationError("check_email", "邮箱验证会话已过期或失效，请重新开始", code)
            self.sleep(min(self.settings.poll_interval, max(0, deadline - self.clock())))
        raise RegistrationError(
            "check_email", "Steam 未在期限内确认邮箱验证", "VERIFICATION_TIMEOUT"
        )

    def _create(
        self, fields: RegistrationInput, page: SignupPageContext, creation_id: str
    ) -> dict[str, Any]:
        for attempt in range(self.settings.account_name_attempts):
            data = self.client.json(
                "/join/checkavail/",
                stage="check_name",
                data={
                    "accountname": fields.account_name,
                    "count": self._count(),
                    "creationid": creation_id,
                },
            )
            available = data.get("bAvailable")
            if type(available) not in {bool, int} or available not in (0, 1):
                raise RegistrationError(
                    "check_name", "账户名检查响应格式不正确", "INVALID_RESPONSE"
                )
            if available:
                break
            if (
                self.account_name_factory is None
                or attempt + 1 >= self.settings.account_name_attempts
            ):
                message = (
                    "Steam 账户名不可用，请指定其他账户名"
                    if self.account_name_factory is None
                    else "自动生成的账户名均被占用，请稍后重试或指定其他账户名"
                )
                raise RegistrationError("check_name", message, "NAME_UNAVAILABLE")
            next_name = self.account_name_factory()
            fields.account_name = RegistrationInput(
                fields.email, next_name, fields.password
            ).account_name
            if self.on_account_name_change is not None:
                self.on_account_name_change(fields)
        data = self.client.json(
            "/join/checkpasswordavail/",
            stage="check_password",
            data={
                "password": fields.password,
                "accountname": fields.account_name,
                "count": self._count(),
            },
        )
        if data.get("bAvailable") not in (True, 1):
            raise RegistrationError(
                "check_password", "Steam 不接受该密码，请更换密码", "PASSWORD_UNAVAILABLE"
            )
        data = self.client.json(
            "/join/createaccount/",
            stage="createaccount",
            data={
                "accountname": fields.account_name,
                "password": fields.password,
                "count": self._count(),
                "lt": page.lt,
                "creation_sessionid": creation_id,
                "embedded_appid": page.embedded_appid,
                "guest": str(page.guest).lower(),
            },
        )
        if not isinstance(data.get("bSuccess"), (bool, int)):
            raise RegistrationError(
                "createaccount", "创建响应缺少 bSuccess，账户状态未知", "OUTCOME_UNKNOWN"
            )
        if data.get("bSuccess") not in (True, 1):
            raise RegistrationError(
                "createaccount",
                "Steam 拒绝创建账户，请检查账户状态后再重试",
                data.get("eresult", "CREATE_REJECTED"),
            )
        return data

    def probe(self) -> dict[str, Any]:
        page = self.open_page()
        challenge = self.refresh_captcha()
        return {
            "init_id": page.init_id,
            "lt": page.lt,
            "captcha_gid": challenge.gid,
            "captcha_type": challenge.kind,
            "sitekey": challenge.sitekey,
            "has_captcha_s": bool(challenge.s),
            "trace": self.client.trace,
        }

    def register(self, fields: RegistrationInput) -> RegistrationResult:
        result = RegistrationResult(
            False, fields.email, fields.account_name, fields.password, trace=self.client.trace
        )
        try:
            page = self.open_page()
            creation_id = self._send_email(fields.email, page)
            link = self.mail_provider.wait_for_link(creation_id, self.settings.mail_max_wait)
            self._follow_link(link, creation_id)
            self._wait_verified(creation_id)
            data = self._create(fields, page, creation_id)
        except RegistrationError as error:
            result.stage, result.code, result.message = error.stage, error.code, str(error)
        else:
            result.success = True
            result.stage = "complete"
            result.code = 1
            result.message = "注册成功"
            steamid = data.get("steamid")
            result.steamid = str(steamid) if isinstance(steamid, (str, int)) else ""
        result.account_name = fields.account_name
        return result
