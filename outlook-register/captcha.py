from __future__ import annotations

import time
from typing import Any

from http_client import BrowserSession
from models import CaptchaSolution


class CaptchaRunClient:
    """PxCaptcha2 adapter recovered from cap.cap.cappx2."""

    captcha_type = "PxCaptcha2"

    def __init__(self, session: BrowserSession, *, api_key: str, base_url: str, poll_interval: float, max_wait: float):
        self.session = session
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    def create_task(self, *, uaid: str, country: str, timezone: int, proxy: str | None = None) -> str:
        payload: dict[str, Any] = {
            "captchaType": self.captcha_type,
            "uaid": uaid,
            "timezone": timezone,
            "country": country,
        }
        if proxy:
            value = proxy.split("://", 1)[-1]
            auth, _, address = value.rpartition("@")
            if not address:
                address, auth = value, ""
            host, _, port = address.partition(":")
            login, _, password = auth.partition(":")
            payload.update({"host": host, "port": port, "login": login, "password": password})
        response = self.session.post(self.base_url, headers=self.headers, json=payload)
        data = self.session.json(response)
        task_id = str(data.get("taskId") or data.get("id") or "")
        if not task_id:
            raise RuntimeError(f"captcha task creation failed: HTTP {response.status_code}: {data}")
        return task_id

    def poll(self, task_id: str) -> CaptchaSolution:
        deadline = time.monotonic() + self.max_wait
        url = f"{self.base_url}/{task_id}?captchaType={self.captcha_type}"
        while time.monotonic() < deadline:
            response = self.session.get(url, headers=self.headers)
            data = self.session.json(response)
            status = str(data.get("status", "")).lower()
            if status in {"success", "succeeded", "finished", "ready"}:
                result = data.get("response") if isinstance(data.get("response"), dict) else data
                return CaptchaSolution(
                    silent_token=str(result.get("silentToken", "")),
                    press_token=str(result.get("pressToken", "")),
                    origin=str(result.get("origin", "")),
                )
            if status in {"fail", "failed", "error"}:
                raise RuntimeError(f"captcha task failed: {data}")
            time.sleep(self.poll_interval)
        raise TimeoutError(f"captcha task timed out: {task_id}")

    def solve(self, *, uaid: str, country: str, timezone: int, proxy: str | None = None) -> CaptchaSolution:
        return self.poll(self.create_task(uaid=uaid, country=country, timezone=timezone, proxy=proxy))
