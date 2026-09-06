"""LuckMail 邮箱订单与 Tavily 验证链接提取。"""

from __future__ import annotations

from luckmail import LuckMailClient

from config import (
    LUCKMAIL_API_KEY,
    LUCKMAIL_BASE_URL,
    LUCKMAIL_DOMAIN,
    LUCKMAIL_EMAIL_TYPE,
    LUCKMAIL_POLL_INTERVAL,
    LUCKMAIL_PROJECT_CODE,
    MAX_EMAIL_WAIT_TIME,
)
from utils import extract_verification_link
from retry_policy import external_request_with_retry


class LuckMailProviderError(RuntimeError):
    """LuckMail 邮箱获取或接码失败。"""


class LuckMailProvider:
    def __init__(self):
        if not LUCKMAIL_API_KEY:
            raise LuckMailProviderError("请设置 LUCKMAIL_API_KEY 环境变量")
        if not LUCKMAIL_PROJECT_CODE:
            raise LuckMailProviderError("请设置 LUCKMAIL_PROJECT_CODE")

        self.client = LuckMailClient(
            base_url=LUCKMAIL_BASE_URL,
            api_key=LUCKMAIL_API_KEY,
        )
        self.order = None
        self.completed = False
        self.verification_link = None

    def acquire_email(self) -> str:
        if self.order:
            return self.order.email_address

        self.order = external_request_with_retry(
            lambda _url: self.client.user.create_order(
                project_code=LUCKMAIL_PROJECT_CODE,
                email_type=LUCKMAIL_EMAIL_TYPE or None,
                domain=LUCKMAIL_DOMAIN or None,
            ),
            "luckmail://create-order",
            node="LuckMail create_order",
        )
        if not self.order.email_address:
            raise LuckMailProviderError("LuckMail 创建订单成功，但未返回邮箱地址")
        return self.order.email_address

    def wait_for_verification_link(self) -> str:
        if not self.order:
            raise LuckMailProviderError("尚未创建 LuckMail 订单")
        if self.verification_link:
            return self.verification_link

        result = external_request_with_retry(
            lambda _url: self.client.user.wait_for_code(
                self.order.order_no,
                timeout=MAX_EMAIL_WAIT_TIME,
                interval=LUCKMAIL_POLL_INTERVAL,
            ),
            "luckmail://wait-for-code",
            node="LuckMail wait_for_code",
        )
        if result.status != "success":
            raise LuckMailProviderError(f"LuckMail 接码失败: {result.status}")

        self.completed = True
        link = extract_verification_link(
            result.mail_body_html,
            result.verification_code,
        )
        if not link:
            raise LuckMailProviderError(
                f"邮件已收到但没有找到 Tavily 验证链接，标题: {result.mail_subject or '未知'}"
            )
        self.verification_link = link
        return link

    def cancel(self) -> None:
        if self.order and not self.completed:
            self.client.user.cancel_order(self.order.order_no)

    def close(self) -> None:
        self.client.close()
