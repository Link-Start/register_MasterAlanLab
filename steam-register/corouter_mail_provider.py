from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import requests

from config import Settings
from models import MailboxRecord, RegistrationError
from utils import extract_verification_link


def _normalise_api_key(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("authorization:"):
        value = value.split(":", 1)[1].strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value


class CorouterMailTransientError(RegistrationError):
    def __init__(self, message: str):
        super().__init__("mail", message, "MAIL_TEMPORARY_ERROR")


class CorouterMailClient:
    def __init__(self, settings: Settings, session: Any = None):
        self.settings = settings
        self.api_key = _normalise_api_key(settings.corouter_api_key)
        if not self.api_key or not settings.corouter_tenant_id:
            raise ValueError(
                "使用 corouter 时请填写 COROUTER_MAIL_API_KEY 和 COROUTER_MAIL_TENANT_ID"
            )
        self.base_url = settings.corouter_base_url.rstrip("/")
        self.tenant_id = settings.corouter_tenant_id
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False

    def request_json(
        self, suffix: str, *, params: dict | None = None, timeout: float | None = None
    ) -> Any:
        url = f"{self.base_url}/api/v1/tenants/{quote(self.tenant_id, safe='')}/mail/{suffix}"
        try:
            response = self.session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                timeout=self.settings.corouter_request_timeout if timeout is None else timeout,
                allow_redirects=False,
            )
        except (requests.RequestException, OSError) as error:
            raise CorouterMailTransientError("Emailbox 连接异常，请检查网络后重试") from error

        status = response.status_code
        if status in {429, 500, 502, 503, 504}:
            raise CorouterMailTransientError(f"Emailbox 暂时不可用（HTTP {status}），稍后重试")
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if status == 401:
            raise RegistrationError(
                "mail", "Emailbox API Key 无效或已重置，请更新配置", "MAIL_AUTH_FAILED"
            )
        if status == 403:
            if isinstance(payload, dict) and payload.get("code") == 1001:
                raise RegistrationError(
                    "mail",
                    "Emailbox 今日取件额度已用尽，请等待额度重置后再运行",
                    "MAIL_QUOTA_EXCEEDED",
                )
            raise RegistrationError(
                "mail", "Emailbox 拒绝访问，请检查 API Key 对应的租户", "MAIL_ACCESS_DENIED"
            )
        if status == 404:
            raise RegistrationError(
                "mail", "Emailbox 邮箱或邮件不存在，请检查邮箱状态", "MAIL_NOT_FOUND"
            )
        if status == 409:
            raise RegistrationError(
                "mail",
                "Emailbox 邮箱不可用，请在网页中检查停用、封禁或邮箱授权状态",
                "MAIL_ACCOUNT_UNAVAILABLE",
            )
        if status != 200:
            raise RegistrationError(
                "mail", f"Emailbox 返回 HTTP {status}，请检查服务状态", "MAIL_SERVICE_ERROR"
            )
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            raise RegistrationError("mail", "Emailbox 响应格式不正确", "MAIL_SERVICE_ERROR")
        if payload["code"] != 0:
            raise RegistrationError(
                "mail",
                f"Emailbox 业务请求失败（code={payload['code']}），请检查服务配置",
                "MAIL_SERVICE_ERROR",
            )
        return payload.get("data")

    @staticmethod
    def items(data: Any) -> list[dict]:
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise RegistrationError("mail", "Emailbox 列表格式不正确", "MAIL_SERVICE_ERROR")
        return items

    def _resolve_group(self) -> str:
        selector = self.settings.corouter_group_id
        if not selector:
            return ""
        for group in self.items(self.request_json("groups")):
            group_id = str(group.get("id") or "")
            if group_id and (
                group_id == selector
                or str(group.get("name") or "").casefold() == selector.casefold()
            ):
                return group_id
        raise RegistrationError(
            "mail", "Emailbox 找不到指定分组，请检查分组 ID 或名称", "MAIL_GROUP_NOT_FOUND"
        )

    def list_mailboxes(self, email: str | None = None) -> list[MailboxRecord]:
        params = {"status": "active", "limit": 200}
        if email:
            params["q"] = email
        group_id = self._resolve_group()
        if group_id:
            params["group_id"] = group_id
        data = self.request_json("accounts", params={**params, "page": 1})
        accounts = self.items(data)
        pagination = data.get("pagination") if isinstance(data, dict) else None
        pages = (pagination or {}).get("pages", 1) if isinstance(pagination, dict) else 1
        if type(pages) is not int or pages < 0:
            raise RegistrationError("mail", "Emailbox 分页信息不正确", "MAIL_SERVICE_ERROR")
        for page in range(2, pages + 1):
            accounts.extend(
                self.items(self.request_json("accounts", params={**params, "page": page}))
            )
        records = []
        seen = set()
        for account in accounts:
            if str(account.get("status") or "active").casefold() != "active":
                continue
            address = str(account.get("email") or "").strip()
            account_id = str(account.get("id") or "").strip()
            if not account_id:
                raise RegistrationError("mail", "Emailbox 邮箱缺少账号 ID", "MAIL_SERVICE_ERROR")
            try:
                record = MailboxRecord(address, account_id=account_id)
            except ValueError as error:
                raise RegistrationError(
                    "mail", "Emailbox 返回了无效的邮箱地址", "MAIL_SERVICE_ERROR"
                ) from error
            if address.casefold() not in seen:
                records.append(record)
                seen.add(address.casefold())
        return records

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def load_corouter_mailboxes(
    settings: Settings, records: list[MailboxRecord] | None = None
) -> list[MailboxRecord]:
    if records == []:
        return []
    with CorouterMailClient(settings) as client:
        available = client.list_mailboxes(
            records[0].email if records and len(records) == 1 else None
        )
    if records is None:
        if not available:
            raise RegistrationError(
                "mail",
                "Emailbox 租户或分组没有可用邮箱，请先在网页中添加并启用邮箱",
                "MAIL_NO_ACCOUNTS",
            )
        return available
    by_email = {record.email.casefold(): record for record in available}
    for record in records:
        match = by_email.get(record.email.casefold())
        if match is None:
            raise RegistrationError(
                "mail",
                "指定邮箱不在 Emailbox 租户或分组的可用列表中，请检查邮箱与分组配置",
                "MAIL_NOT_FOUND",
            )
        record.account_id = match.account_id
    return records


class CorouterMailProvider:
    def __init__(
        self,
        record: MailboxRecord,
        settings: Settings,
        session: Any = None,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.settings = settings
        self.client = CorouterMailClient(settings, session)
        self.clock = clock
        self.sleep = sleep
        self.deadline = 0.0
        self.seen_messages: set[tuple[str, str, str]] = set()
        self.account_id = record.account_id
        if not self.account_id:
            try:
                match = next(
                    (
                        mailbox
                        for mailbox in self.client.list_mailboxes(record.email)
                        if mailbox.email.casefold() == record.email.casefold()
                    ),
                    None,
                )
                if match is None:
                    raise RegistrationError(
                        "mail", "Emailbox 找不到指定的可用邮箱", "MAIL_NOT_FOUND"
                    )
                self.account_id = match.account_id
            except (ValueError, RegistrationError):
                self.close()
                raise

    def _request_timeout(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise RegistrationError("mail", "等待 Steam 验证邮件超时", "MAIL_TIMEOUT")
        return min(self.settings.corouter_request_timeout, remaining)

    @staticmethod
    def _link(message: dict, creation_id: str) -> str | None:
        for field in ("body_preview", "body", "subject"):
            text = message.get(field)
            if isinstance(text, str):
                link = extract_verification_link(text, creation_id)
                if link:
                    return link
        return None

    def _poll_link(self, creation_id: str) -> str | None:
        path = f"accounts/{quote(self.account_id, safe='')}/messages"
        data = self.client.request_json(
            path,
            params={"folder": "all", "top": min(self.settings.mail_scan_limit, 50), "skip": 0},
            timeout=self._request_timeout(),
        )
        for message in self.client.items(data):
            link = self._link(message, creation_id)
            if link:
                return link
            # 只为 Steam 邮件获取正文，避免为无关邮件消耗取件额度。
            metadata = " ".join(
                str(message.get(field) or "") for field in ("subject", "from", "body_preview")
            )
            if metadata.strip() and "steam" not in metadata.casefold():
                continue
            message_id = message.get("id")
            if message_id is None or message_id == "":
                continue
            folder = str(message.get("folder") or "inbox")
            id_mode = message.get("id_mode")
            key = (folder, str(message_id), str(id_mode))
            if key in self.seen_messages:
                continue
            params = {"folder": folder}
            if id_mode is not None:
                params["id_mode"] = id_mode
            detail = self.client.request_json(
                f"{path}/{quote(str(message_id), safe='')}",
                params=params,
                timeout=self._request_timeout(),
            )
            if not isinstance(detail, dict):
                raise RegistrationError("mail", "Emailbox 邮件正文格式不正确", "MAIL_SERVICE_ERROR")
            # 失败的正文请求不记入缓存，下次轮询仍可读取。
            self.seen_messages.add(key)
            link = self._link(detail, creation_id)
            if link:
                return link
        return None

    def wait_for_link(self, creation_id: str, timeout: float) -> str:
        self.deadline = self.clock() + timeout
        backoff = max(1.0, self.settings.corouter_poll_interval)
        had_network_error = False
        while self.clock() < self.deadline:
            try:
                link = self._poll_link(creation_id)
                if link:
                    return link
                delay = self.settings.corouter_poll_interval
                backoff = max(1.0, delay)
            except CorouterMailTransientError:
                had_network_error = True
                delay = backoff
                backoff = min(backoff * 2, 60)
            self.sleep(min(delay, max(0, self.deadline - self.clock())))
        message = (
            "Emailbox 等待期间出现网络异常，未取得 Steam 邮件；请检查服务后重试"
            if had_network_error
            else "等待 Steam 验证邮件超时，请检查邮箱状态或增大 MAIL_MAX_WAIT"
        )
        raise RegistrationError("mail", message, "MAIL_TIMEOUT")

    def close(self) -> None:
        self.client.close()
