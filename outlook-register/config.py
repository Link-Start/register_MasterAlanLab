from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
)


@dataclass(slots=True)
class Settings:
    proxy: str | None = None
    timeout: float = 30.0
    country: str = "US"
    market: str | None = None
    timezone: int = 480
    user_agent: str = DEFAULT_USER_AGENT
    captcha_run_key: str | None = None
    captcha_run_base_url: str = "https://api.captcha-run.com/v2/tasks"
    captcha_poll_interval: float = 3.0
    captcha_max_wait: float = 60.0
    output_path: Path = Path("accounts.txt")

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Settings:
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        def number(name: str, default: float) -> float:
            value = os.getenv(name)
            return default if value in (None, "") else float(value)

        timezone = int(os.getenv("MS_REGISTER_TIMEZONE", "480"))
        output = Path(os.getenv("MS_REGISTER_OUTPUT", "accounts.txt"))
        return cls(
            proxy=os.getenv("MS_REGISTER_PROXY") or None,
            timeout=number("MS_REGISTER_TIMEOUT", 30.0),
            country=os.getenv("MS_REGISTER_COUNTRY", "US"),
            market=os.getenv("MS_REGISTER_MARKET") or None,
            timezone=timezone,
            user_agent=os.getenv("MS_REGISTER_USER_AGENT") or DEFAULT_USER_AGENT,
            captcha_run_key=os.getenv("CAPTCHA_RUN_KEY") or None,
            captcha_run_base_url=os.getenv(
                "CAPTCHA_RUN_BASE_URL", "https://api.captcha-run.com/v2/tasks"
            ).rstrip("/"),
            captcha_poll_interval=number("CAPTCHA_RUN_POLL_INTERVAL", 3.0),
            captcha_max_wait=number("CAPTCHA_RUN_MAX_WAIT", 60.0),
            output_path=output,
        )
