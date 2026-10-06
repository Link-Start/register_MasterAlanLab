from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from curl_cffi import requests

from config import Settings
from models import RegistrationError
from utils import STEAM_ORIGIN


class SteamSession:
    """一条注册链路只使用一个 Chrome 会话和一个代理出口。"""

    def __init__(self, settings: Settings, session: Any = None):
        self.timeout = settings.timeout
        self.session = (
            session
            if session is not None
            else requests.Session(
                impersonate=settings.impersonate,
                proxies={"http": settings.proxy, "https": settings.proxy} if settings.proxy else {},
                trust_env=False,
            )
        )
        self.trace: list[dict[str, Any]] = []

    def request(self, method: str, url: str, *, stage: str, **kwargs: Any):
        if url.startswith("/"):
            url = STEAM_ORIGIN + url
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", False)
        event = {"stage": stage, "method": method, "path": urlsplit(url).path}
        self.trace.append(event)
        try:
            response = self.session.request(method, url, **kwargs)
        except requests.exceptions.RequestException as error:
            event["error"] = type(error).__name__
            # 异常文本可能含代理密码或 stoken，因此只报告异常类型。
            code = "OUTCOME_UNKNOWN" if stage == "createaccount" else "NETWORK_ERROR"
            message = f"网络请求失败（{type(error).__name__}），请检查网络或代理"
            if stage == "createaccount":
                message = "创建请求未收到响应，账户状态未知；请先检查邮箱或登录，再决定是否重试"
            raise RegistrationError(stage, message, code) from error
        event["http_status"] = response.status_code
        if response.status_code == 429:
            raise RegistrationError(stage, "Steam 返回 HTTP 429，请稍后再试", "RATE_LIMITED")
        if not 200 <= response.status_code < 400:
            code = "OUTCOME_UNKNOWN" if stage == "createaccount" else response.status_code
            message = f"Steam 返回 HTTP {response.status_code}"
            if stage == "createaccount":
                message += "；创建结果未知，请先检查账户状态"
            raise RegistrationError(stage, message, code)
        return response

    def json(
        self, path: str, *, stage: str, data: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        response = self.request(
            "POST",
            path,
            stage=stage,
            data=data,
            timeout=self.timeout if timeout is None else timeout,
            headers={
                "Accept": "text/javascript, text/html, application/xml, text/xml, */*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": STEAM_ORIGIN,
                "Referer": f"{STEAM_ORIGIN}/join/",
                "X-Requested-With": "XMLHttpRequest",
                "X-Prototype-Version": "1.7",
            },
        )
        try:
            result = response.json()
        except ValueError as error:
            code = "OUTCOME_UNKNOWN" if stage == "createaccount" else "INVALID_RESPONSE"
            raise RegistrationError(
                stage, "Steam 未返回 JSON；请检查是否出现风控页面", code
            ) from error
        if not isinstance(result, dict):
            code = "OUTCOME_UNKNOWN" if stage == "createaccount" else "INVALID_RESPONSE"
            raise RegistrationError(stage, "Steam JSON 响应格式不正确", code)
        status = result.get("success", result.get("bSuccess"))
        if isinstance(status, (str, int, bool)):
            self.trace[-1]["result_code"] = status
        return result

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> SteamSession:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
