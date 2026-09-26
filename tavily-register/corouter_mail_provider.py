"""Emailbox (mail.corouter.cc) provider for Tavily verification mail.

The Emailbox API is intentionally read-only: a mailbox is selected from the
tenant's existing accounts and its inbox is polled until the Tavily link
arrives.  Credentials are read from environment variables via ``config`` and
are never written to output files or logs.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from config import (
    COROUTER_MAIL_API_KEY,
    COROUTER_MAIL_BASE_URL,
    COROUTER_MAIL_GROUP_ID,
    COROUTER_MAIL_POLL_INTERVAL,
    COROUTER_MAIL_REQUEST_RETRIES,
    COROUTER_MAIL_REQUEST_TIMEOUT,
    COROUTER_MAIL_TENANT_ID,
    MAX_EMAIL_WAIT_TIME,
)
from retry_policy import TRANSIENT_HTTP_STATUSES, external_request_with_retry
from utils import extract_verification_link


class CorouterMailProviderError(RuntimeError):
    """Mailbox discovery, API, or polling failure."""


class CorouterMailTransientError(CorouterMailProviderError):
    """A temporary Emailbox outage (for example HTTP 502)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CorouterMailRecoveredWithoutLink(CorouterMailProviderError):
    """The mailbox is readable again but contains no verification link."""


class CorouterMailTimeout(CorouterMailProviderError):
    """The mailbox deadline elapsed without a usable verification link."""


_ACCOUNT_CURSOR_LOCK = threading.Lock()
_ACCOUNT_CURSORS: dict[tuple[str, str], int] = {}


def _normalise_api_key(value: str) -> str:
    """Accept either a raw key or a copied ``Authorization: Bearer`` value."""

    value = str(value or "").strip()
    if value.lower().startswith("authorization:"):
        value = value.split(":", 1)[1].strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value


class CorouterMailProvider:
    """Read existing tenant mailboxes through mail.corouter.cc."""

    def __init__(self, session=None, group_id: str | None = None):
        api_key = _normalise_api_key(COROUTER_MAIL_API_KEY)
        if not api_key:
            raise CorouterMailProviderError(
                "请设置 COROUTER_MAIL_API_KEY（或 Authorization: Bearer ...）"
            )
        if not COROUTER_MAIL_TENANT_ID:
            raise CorouterMailProviderError(
                "请设置 COROUTER_MAIL_TENANT_ID（Emailbox API 页面中的租户 ID）"
            )

        self.base_url = COROUTER_MAIL_BASE_URL.rstrip("/")
        self.tenant_id = str(COROUTER_MAIL_TENANT_ID).strip()
        self.group_selector = str(
            group_id if group_id is not None else COROUTER_MAIL_GROUP_ID
        ).strip()
        self.group_id: str | None = None
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
        )
        self.account_id: str | None = None
        self.email: str | None = None
        self.completed = False
        self.verification_link: str | None = None
        self._started_at = datetime.now(timezone.utc)
        self._seen_message_ids: set[str] = set()

    def _path(self, suffix: str) -> str:
        return (
            f"{self.base_url}/api/v1/tenants/{quote(self.tenant_id, safe='')}"
            f"/mail/{suffix.lstrip('/')}"
        )

    def _request_json(
        self,
        path: str,
        *,
        params: dict | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ):
        url = path if path.startswith("http") else self._path(path)
        request_timeout = (
            COROUTER_MAIL_REQUEST_TIMEOUT
            if timeout is None
            else max(0.1, float(timeout))
        )

        def request(_url):
            return self.session.get(
                _url,
                params=params,
                timeout=request_timeout,
            )

        try:
            response = external_request_with_retry(
                request,
                url,
                node=f"Emailbox {path}",
                max_attempts=(
                    COROUTER_MAIL_REQUEST_RETRIES
                    if max_attempts is None
                    else max(1, int(max_attempts))
                ),
                retry_delay=1.0,
            )
        except Exception as exc:
            # A transport timeout/connection reset is recoverable in exactly
            # the same way as a 502 response.  The polling loop applies the
            # longer, deadline-aware backoff; this short request retry only
            # handles transient blips within one poll.
            raise CorouterMailTransientError(f"Emailbox 请求失败: {exc}") from exc

        status = getattr(response, "status_code", 200)
        try:
            payload = response.json()
        except Exception as exc:
            message = f"Emailbox 返回了无效 JSON (HTTP {status})"
            if status in TRANSIENT_HTTP_STATUSES:
                raise CorouterMailTransientError(
                    message, status_code=status
                ) from exc
            raise CorouterMailProviderError(message) from exc

        if status >= 400:
            message = payload.get("message") if isinstance(payload, dict) else payload
            code = payload.get("code") if isinstance(payload, dict) else None
            code_text = f" code={code}" if code not in (None, 0) else ""
            error_message = (
                f"Emailbox API HTTP {status}{code_text}: {message or '请求失败'}"
            )
            if status in TRANSIENT_HTTP_STATUSES:
                raise CorouterMailTransientError(
                    error_message, status_code=status
                )
            raise CorouterMailProviderError(error_message)
        if not isinstance(payload, dict):
            raise CorouterMailProviderError("Emailbox 响应格式异常")
        code = payload.get("code", 0)
        if code not in (0, None):
            raise CorouterMailProviderError(
                f"Emailbox API 错误 {code}: {payload.get('message') or '请求失败'}"
            )
        return payload.get("data")

    @staticmethod
    def _items(data) -> list[dict]:
        if isinstance(data, dict):
            items = data.get("items", [])
        else:
            items = data
        return [item for item in (items or []) if isinstance(item, dict)]

    def _list_accounts(self, email: str | None = None) -> list[dict]:
        self._resolve_group_selector()
        params = {"page": 1, "limit": 200, "status": "active"}
        if email:
            params["q"] = email
        if self.group_id:
            params["group_id"] = self.group_id
        data = self._request_json("accounts", params=params)
        accounts = self._items(data)

        # The documented limit is 200. Continue through additional pages when
        # a tenant has more mailboxes than that.
        pagination = data.get("pagination") if isinstance(data, dict) else None
        pages = int((pagination or {}).get("pages") or 1)
        for page in range(2, pages + 1):
            params["page"] = page
            accounts.extend(self._items(self._request_json("accounts", params=params)))
        return accounts

    def _resolve_group_selector(self) -> None:
        """Resolve a group ID or a human-readable group name once per provider."""

        if not self.group_selector or self.group_id:
            return
        data = self._request_json("groups")
        groups = self._items(data)
        selector = self.group_selector.casefold()
        for group in groups:
            group_id = str(group.get("id") or "").strip()
            group_name = str(group.get("name") or "").strip()
            if group_id == self.group_selector or group_name.casefold() == selector:
                self.group_id = group_id
                return
        raise CorouterMailProviderError(
            f"Emailbox 找不到邮箱分组: {self.group_selector}"
        )

    def acquire_email(self) -> str:
        if self.email:
            return self.email

        accounts = self._list_accounts()
        usable = [
            account
            for account in accounts
            if str(account.get("email") or "").strip()
            and str(account.get("status") or "active").lower() == "active"
        ]
        if not usable:
            raise CorouterMailProviderError("Emailbox 没有可用的 active 邮箱账号")

        key = (self.base_url, self.tenant_id)
        with _ACCOUNT_CURSOR_LOCK:
            index = _ACCOUNT_CURSORS.get(key, 0) % len(usable)
            _ACCOUNT_CURSORS[key] = index + 1
        account = usable[index]
        self.account_id = str(account.get("id") or "").strip() or None
        self.email = str(account.get("email") or "").strip()
        if not self.account_id:
            raise CorouterMailProviderError("Emailbox 邮箱账号缺少 id")
        self._started_at = datetime.now(timezone.utc)
        return self.email

    def _resolve_account(self) -> None:
        if self.account_id:
            return
        if not self.email:
            raise CorouterMailProviderError("尚未选择 Emailbox 邮箱")
        accounts = self._list_accounts(self.email)
        account = next(
            (
                item
                for item in accounts
                if str(item.get("email") or "").strip().lower() == self.email.lower()
            ),
            None,
        )
        if not account or not account.get("id"):
            raise CorouterMailProviderError(f"Emailbox 找不到邮箱账号: {self.email}")
        self.account_id = str(account["id"])

    @staticmethod
    def _is_new_message(message: dict, started_at: datetime) -> bool:
        received_at = message.get("received_at")
        if not received_at:
            return True
        try:
            parsed = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed >= started_at
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _message_link(message: dict) -> str | None:
        if not isinstance(message, dict):
            return None
        return extract_verification_link(
            message.get("subject"),
            message.get("from"),
            message.get("to"),
            message.get("body_preview"),
            message.get("body"),
            message.get("text"),
            message.get("html"),
            message.get("verification_code"),
        )

    def _read_messages(
        self, *, timeout: float | None = None, max_attempts: int | None = None
    ) -> list[dict]:
        data = self._request_json(
            f"accounts/{quote(str(self.account_id), safe='')}/messages",
            params={"folder": "all", "top": 50, "skip": 0},
            timeout=timeout,
            max_attempts=max_attempts,
        )
        return self._items(data)

    def _read_message_detail(
        self,
        message: dict,
        *,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        message_id = message.get("id")
        if message_id is None:
            return None
        key = f"{message.get('folder') or 'inbox'}:{message_id}:{message.get('id_mode') or ''}"
        if key in self._seen_message_ids:
            return None
        # The documented value is ``junk``, while Outlook/IMAP-backed
        # accounts may return the concrete folder name ``junkemail``.
        # Preserve the value returned by the list endpoint for the detail
        # request; the API accepts that concrete name as well.
        folder = str(message.get("folder") or "inbox").lower()
        if folder not in {"inbox", "junk", "junkemail"}:
            folder = "inbox"
        params = {
            "folder": folder,
            "id_mode": message.get("id_mode") or "",
        }
        data = self._request_json(
            f"accounts/{quote(str(self.account_id), safe='')}/messages/"
            f"{quote(str(message_id), safe='')}",
            params=params,
            timeout=timeout,
            max_attempts=max_attempts,
        )
        # Mark a message only after the detail request succeeds. A temporary
        # 502 must not hide its contents on the next poll.
        self._seen_message_ids.add(key)
        return data if isinstance(data, dict) else None

    def wait_for_verification_link(
        self,
        *,
        timeout: float | None = None,
        poll_interval: float | None = None,
    ) -> str:
        """Poll this mailbox with a deadline-aware outage backoff.

        Emailbox occasionally returns a burst of HTTP 502 responses while a
        folder is being refreshed.  A request retry must not restart the
        five-minute budget, so message requests use one short attempt and the
        polling loop performs exponential backoff (1, 2, 4 ... 60 seconds).
        Once a temporarily broken folder becomes readable, an empty result is
        considered a stale mailbox and is returned to the batch orchestrator
        immediately instead of waiting another five minutes.
        """
        if self.verification_link:
            return self.verification_link
        if not self.email:
            raise CorouterMailProviderError("尚未生成 Emailbox 邮箱")
        self._resolve_account()

        wait_seconds = min(
            300.0,
            max(
                0.0,
                float(MAX_EMAIL_WAIT_TIME if timeout is None else timeout),
            ),
        )
        interval = (
            COROUTER_MAIL_POLL_INTERVAL
            if poll_interval is None
            else max(0.1, float(poll_interval))
        )
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        last_error = None
        had_transient_error = False
        backoff = 1.0
        first_poll = True
        while first_poll or time.monotonic() < deadline:
            first_poll = False
            remaining = max(0.1, deadline - time.monotonic())
            try:
                # One request per poll keeps the global five-minute deadline
                # authoritative even when the upstream request itself times
                # out.  The outer loop is the long-lived retry mechanism.
                messages = self._read_messages(
                    timeout=min(float(COROUTER_MAIL_REQUEST_TIMEOUT), remaining),
                    max_attempts=1,
                )
                found_link = None
                for message in messages:
                    if not self._is_new_message(message, self._started_at):
                        continue
                    link = self._message_link(message)
                    if not link:
                        detail = self._read_message_detail(
                            message,
                            timeout=min(
                                float(COROUTER_MAIL_REQUEST_TIMEOUT),
                                max(0.1, deadline - time.monotonic()),
                            ),
                            max_attempts=1,
                        )
                        link = self._message_link(detail or {})
                    if link:
                        found_link = link
                        break
                if found_link:
                    self.completed = True
                    self.verification_link = found_link
                    return found_link

                # If the folder was unavailable and then came back without a
                # Tavily link, this address is known to be stale.  Let the
                # batch layer close it and acquire the next mailbox now.
                if had_transient_error:
                    raise CorouterMailRecoveredWithoutLink(
                        "Emailbox 收件箱恢复，但未找到 Tavily 验证链接；跳过当前邮箱"
                    )
                last_error = None
                backoff = 1.0
                delay = min(interval, max(0.0, deadline - time.monotonic()))
            except CorouterMailProviderError as exc:
                last_error = exc
                if isinstance(exc, CorouterMailTransientError):
                    had_transient_error = True
                else:
                    # A recovered-but-empty mailbox or a permanent API error
                    # is terminal for this address. Only transient transport
                    # and HTTP failures participate in outage backoff.
                    raise
                delay = min(backoff, max(0.0, deadline - time.monotonic()))
                print(
                    f"    [邮箱退避重试] {self.email} 暂时不可用，"
                    f"{delay:g} 秒后重试（剩余 {max(0, int(deadline - time.monotonic()))} 秒）"
                )
                backoff = min(backoff * 2.0, 60.0)

            if delay <= 0 or time.monotonic() >= deadline:
                break
            time.sleep(delay)

        suffix = f": {last_error}" if last_error else ""
        raise CorouterMailTimeout(
            f"等待 Emailbox 的 Tavily 验证邮件超时（已等待 {wait_seconds:g} 秒）{suffix}"
        )

    def cancel(self) -> None:
        # Emailbox exposes read-only API endpoints; accounts remain managed by
        # the tenant and are not cancelled by a registration attempt.
        return None

    def close(self) -> None:
        self.session.close()


# Emailbox is the product name used by the API documentation; keep aliases so
# callers can use either the product or host-oriented provider name.
EmailboxProvider = CorouterMailProvider
EmailboxProviderError = CorouterMailProviderError

__all__ = [
    "CorouterMailProvider",
    "CorouterMailProviderError",
    "CorouterMailTransientError",
    "CorouterMailRecoveredWithoutLink",
    "CorouterMailTimeout",
    "EmailboxProvider",
    "EmailboxProviderError",
]
