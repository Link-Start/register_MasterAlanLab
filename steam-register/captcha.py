from __future__ import annotations

import getpass
import time
from typing import Any
from urllib.parse import quote

import requests

from config import Settings
from models import CaptchaChallenge, RegistrationError
from utils import STEAM_ORIGIN


class CaptchaTransientError(RegistrationError):
    def __init__(self, message: str):
        super().__init__("captcha", message, "CAPTCHA_SERVICE_ERROR")


class ManualCaptchaSolver:
    def __init__(self, token: str = "", prompt: Any = getpass.getpass):
        self.token = token
        self.prompt = prompt

    def solve(self, challenge: CaptchaChallenge) -> str:
        if challenge.gid == "-1":
            return ""
        if self.token:
            token, self.token = self.token, ""
            return token
        description = f"gid={challenge.gid}, type={challenge.kind}, sitekey={challenge.sitekey}"
        if challenge.kind == 1:
            description = f"图片：{STEAM_ORIGIN}/login/rendercaptcha?gid={challenge.gid}"
        try:
            token = self.prompt(f"输入本次验证码文字或 token（{description}）：").strip()
        except (EOFError, OSError) as error:
            raise RegistrationError(
                "captcha",
                "无法读取验证码；请配置 CAPTCHA_RUN_KEY 或提供 --captcha-token",
                "CAPTCHA_REQUIRED",
            ) from error
        if not token:
            raise RegistrationError("captcha", "验证码不能为空", "CAPTCHA_REQUIRED")
        return token

    def close(self) -> None:
        pass


class CaptchaRunClient:
    captcha_type = "HCaptchaSteam"

    def __init__(
        self, settings: Settings, session: Any = None, *, clock=time.monotonic, sleep=time.sleep
    ):
        self.settings = settings
        self.session = session if session is not None else requests.Session()
        # 打码服务直连，不复用 Steam Cookie、代理或系统环境代理。
        self.session.trust_env = False
        self.clock = clock
        self.sleep = sleep

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.session.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self.settings.captcha_key}",
                    "Content-Type": "application/json",
                },
                **kwargs,
            )
        except requests.RequestException as error:
            raise CaptchaTransientError(
                f"打码服务连接失败（{type(error).__name__}），请检查网络"
            ) from error
        if response.status_code in {429, 500, 502, 503, 504}:
            raise CaptchaTransientError(f"打码服务暂时不可用（HTTP {response.status_code}）")
        try:
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            raise RegistrationError(
                "captcha",
                f"打码服务请求失败（{type(error).__name__}），请检查配置或网络",
                "CAPTCHA_SERVICE_ERROR",
            ) from error
        if not isinstance(data, dict):
            raise RegistrationError("captcha", "打码服务响应格式不正确", "INVALID_RESPONSE")
        return data

    def solve(self, challenge: CaptchaChallenge) -> str:
        if challenge.gid == "-1":
            return ""
        if challenge.kind != 3 or not challenge.sitekey:
            raise RegistrationError(
                "captcha",
                "HCaptchaSteam 仅支持 hCaptcha；请用 --captcha-token 提供当前挑战的结果",
                "UNSUPPORTED_CAPTCHA",
            )
        if not self.settings.captcha_key:
            raise RegistrationError("captcha", "请配置 CAPTCHA_RUN_KEY", "CAPTCHA_REQUIRED")
        deadline = self.clock() + self.settings.captcha_max_wait
        base_url = self.settings.captcha_base_url.rstrip("/")
        data = self._request(
            "POST",
            base_url,
            json={
                "captchaType": self.captcha_type,
                "siteReferer": f"{STEAM_ORIGIN}/join/",
                "siteKey": challenge.sitekey,
                "developer": "542f4f4f-31b6-4b70-b485-c4762c45d1e8",
            },
            timeout=min(self.settings.timeout, self.settings.captcha_max_wait),
        )
        task_id = data.get("taskId")
        if isinstance(task_id, bool) or not isinstance(task_id, (str, int)) or not str(task_id):
            raise RegistrationError("captcha", "打码服务未返回 taskId", "INVALID_RESPONSE")
        while self.clock() < deadline:
            try:
                data = self._request(
                    "GET",
                    f"{base_url}/{quote(str(task_id), safe='')}",
                    timeout=min(self.settings.timeout, deadline - self.clock()),
                )
            except CaptchaTransientError:
                self.sleep(
                    min(self.settings.captcha_poll_interval, max(0, deadline - self.clock()))
                )
                continue
            status = str(data.get("status", "")).lower()
            if status == "success":
                response = data.get("response")
                token = next(
                    (
                        value.strip()
                        for field in ("token", "captcha_key")
                        if isinstance(response, dict)
                        and isinstance(value := response.get(field), str)
                        and value.strip()
                    ),
                    "",
                )
                if not token:
                    raise RegistrationError(
                        "captcha",
                        "打码结果缺少有效验证码 token，请检查服务结果",
                        "INVALID_RESPONSE",
                    )
                gid = response.get("gid")
                if gid is not None:
                    if (
                        isinstance(gid, bool)
                        or not isinstance(gid, (str, int))
                        or not str(gid).strip()
                    ):
                        raise RegistrationError(
                            "captcha", "打码结果中的验证码编号无效", "INVALID_RESPONSE"
                        )
                    challenge.gid = str(gid).strip()
                return token
            if status in {"fail", "failed"}:
                raise RegistrationError(
                    "captcha", "打码任务识别失败，请检查服务控制台", "CAPTCHA_FAILED"
                )
            if status not in {"working", "processing", "pending"}:
                raise RegistrationError("captcha", "打码服务返回未知任务状态", "INVALID_RESPONSE")
            self.sleep(min(self.settings.captcha_poll_interval, max(0, deadline - self.clock())))
        raise RegistrationError("captcha", "等待打码结果超时", "CAPTCHA_TIMEOUT")

    def close(self) -> None:
        self.session.close()
