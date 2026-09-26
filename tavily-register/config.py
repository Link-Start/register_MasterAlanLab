"""从唯一的 ``config.yaml`` 文件读取运行配置。"""

from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().with_name("config.yaml")


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """读取 YAML 配置；本地文件由 ``config.yaml.example`` 复制得到。"""
    path = Path(config_path) if config_path is not None else CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {path}；请先复制 config.yaml.example 为 config.yaml"
        )
    with path.open("r", encoding="utf-8") as file_obj:
        values = yaml.safe_load(file_obj) or {}
    if not isinstance(values, dict):
        raise ValueError(f"配置文件必须是 YAML 对象: {path}")
    return values


def _load_optional_config() -> dict[str, Any]:
    """导入工具模块时保留默认值；正式运行由 ``load_config`` 校验文件。"""
    if not CONFIG_PATH.exists():
        return {}
    return load_config()


_CONFIG = _load_optional_config()


def _value(name: str, default: Any = None) -> Any:
    value = _CONFIG.get(name, default)
    return default if value is None else value


def _text(name: str, default: str = "") -> str:
    return str(_value(name, default) or "").strip()


def _int(name: str, default: int) -> int:
    return int(_value(name, default))


def _float(name: str, default: float) -> float:
    return float(_value(name, default))

# Tavily 相关配置
TAVILY_MARKETING_URL = "https://www.tavily.com/"
TAVILY_HOME_URL = "https://app.tavily.com/home"

# Output stays beside the script even when main.py is launched from another directory.
API_KEYS_FILE = str(Path(__file__).resolve().with_name("api_keys.txt"))

MAX_EMAIL_WAIT_TIME = _int("MAX_EMAIL_WAIT_TIME", 300)

# 临时邮箱 provider
EMAIL_PROVIDER = _text("EMAIL_PROVIDER", "corouter")

# 代理 API 配置
PROXY_API_URL = _text("PROXY_API_URL")

# 浏览器配置
HEADLESS = _text("HEADLESS", "false").lower() in ("1", "true", "yes")
BROWSER_TIMEOUT = _int("BROWSER_TIMEOUT", 30000)
HUMAN_DELAY_MIN = max(0.0, _float("HUMAN_DELAY_MIN", 0.8))
HUMAN_DELAY_MAX = max(HUMAN_DELAY_MIN, _float("HUMAN_DELAY_MAX", 2.4))

# 比特浏览器 Local API 配置
BIT_BROWSER_API_URL = _text("BIT_BROWSER_API_URL", "http://127.0.0.1:54346")
BIT_BROWSER_ID = _text("BIT_BROWSER_ID")
BIT_BROWSER_NAME = _text("BIT_BROWSER_NAME", "tavily-register")
BIT_BROWSER_CLOSE_WAIT = max(5.0, _float("BIT_BROWSER_CLOSE_WAIT", 5))

# LuckMail 配置
LUCKMAIL_BASE_URL = _text("LUCKMAIL_BASE_URL", "https://mails.luckyous.com")
LUCKMAIL_API_KEY = _text("LUCKMAIL_API_KEY")
LUCKMAIL_PROJECT_CODE = _text("LUCKMAIL_PROJECT_CODE", "grok")
LUCKMAIL_EMAIL_TYPE = _text("LUCKMAIL_EMAIL_TYPE", "ms_graph")
LUCKMAIL_DOMAIN = _text("LUCKMAIL_DOMAIN", "outlook.com")
LUCKMAIL_POLL_INTERVAL = _float("LUCKMAIL_POLL_INTERVAL", 3)

# Emailbox (mail.corouter.cc) 配置
COROUTER_MAIL_BASE_URL = _text(
    "COROUTER_MAIL_BASE_URL", "https://mail.corouter.cc"
).rstrip("/")
COROUTER_MAIL_API_KEY = _text("COROUTER_MAIL_API_KEY")
COROUTER_MAIL_TENANT_ID = _text("COROUTER_MAIL_TENANT_ID")
COROUTER_MAIL_GROUP_ID = _text("COROUTER_MAIL_GROUP_ID")
COROUTER_MAIL_POLL_INTERVAL = _float("COROUTER_MAIL_POLL_INTERVAL", 5)
COROUTER_MAIL_REQUEST_TIMEOUT = _float("COROUTER_MAIL_REQUEST_TIMEOUT", 65)
COROUTER_MAIL_REQUEST_RETRIES = max(1, _int("COROUTER_MAIL_REQUEST_RETRIES", 3))
