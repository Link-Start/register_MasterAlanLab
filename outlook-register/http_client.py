from __future__ import annotations

from typing import Any

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - dependency is installed by uv
    curl_requests = None

import requests


class BrowserSession:
    """Small requests wrapper matching the compiled tool's curl_cffi session."""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout: float = 30.0,
        user_agent: str,
        language: str = "en-US",
    ):
        self.timeout = timeout
        if curl_requests is not None:
            self.session = curl_requests.Session(impersonate="chrome146")
        else:
            self.session = requests.Session()

        self.session.headers.update(
            {
                "Accept-Language": f"{language},{language.split('-')[0]};q=0.9",
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate, br, zstd",
            }
        )
        if proxy:
            if "://" not in proxy:
                proxy = f"http://{proxy}"
            self.session.proxies = {"http": proxy, "https": proxy}

    @property
    def cookies(self):
        return self.session.cookies

    def get(self, url: str, **kwargs: Any):
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        kwargs.setdefault("timeout", self.timeout)
        return self.session.post(url, **kwargs)

    @staticmethod
    def json(response: Any) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {"data": value}
