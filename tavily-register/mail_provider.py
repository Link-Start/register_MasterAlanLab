"""Email provider selection."""

from config import EMAIL_PROVIDER
from corouter_mail_provider import CorouterMailProvider
from luckmail_provider import LuckMailProvider


def create_mail_provider(*, group_id: str | None = None):
    provider = EMAIL_PROVIDER.strip().lower().replace("-", "_")
    if provider in {"corouter", "corouter_mail", "emailbox"}:
        return CorouterMailProvider(group_id=group_id)
    if provider == "luckmail":
        return LuckMailProvider()
    raise ValueError(
        f"不支持的 EMAIL_PROVIDER: {EMAIL_PROVIDER}，可选值: corouter, luckmail"
    )
