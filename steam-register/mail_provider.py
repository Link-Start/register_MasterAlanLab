from __future__ import annotations

import imaplib
import poplib
import re
import time
from pathlib import Path
from typing import Any

import requests

from config import Settings
from corouter_mail_provider import CorouterMailProvider
from models import MailboxRecord, RegistrationError
from utils import extract_verification_link, mail_texts, verification_url

TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
CLIENT_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def parse_mailbox(line: str) -> MailboxRecord:
    # 兼容标准格式和辅助导出的 refresh_token/client_id 反序格式。
    separator = next((value for value in ("----", "|", "\t") if value in line), None)
    fields = line.split(separator) if separator else [line]
    if len(fields) == 1:
        return MailboxRecord(fields[0])
    if len(fields) == 2:
        return MailboxRecord(fields[0], fields[1])
    if len(fields) != 4:
        raise ValueError("邮箱记录须为 1、2 或 4 段，分隔符支持 ----、|、Tab")
    third, fourth = fields[2].strip(), fields[3].strip()
    if CLIENT_ID.fullmatch(third) and not CLIENT_ID.fullmatch(fourth) and fourth:
        return MailboxRecord(fields[0], fields[1], third, fourth)
    if CLIENT_ID.fullmatch(fourth) and not CLIENT_ID.fullmatch(third) and third:
        return MailboxRecord(fields[0], fields[1], fourth, third)
    raise ValueError("四段记录必须含一个 UUID 格式的 client_id 和一个 refresh_token")


def load_mailboxes(path: str | Path) -> list[MailboxRecord]:
    records: list[MailboxRecord] = []
    seen: set[str] = set()
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            record = parse_mailbox(line)
        except ValueError as error:
            # 导入错误只显示行号，不显示原始密码或 refresh token。
            raise ValueError(f"邮箱文件第 {line_number} 行：{error}") from error
        if record.email.casefold() not in seen:
            records.append(record)
            seen.add(record.email.casefold())
    if not records:
        raise ValueError("邮箱文件没有可用记录")
    return records


def _response_json(response: Any) -> dict[str, Any]:
    if response.status_code in {429, 500, 502, 503, 504}:
        response.raise_for_status()
    if response.status_code != 200:
        raise RegistrationError(
            "mail",
            f"邮箱服务返回 HTTP {response.status_code}，请检查权限或凭据",
            "MAIL_SERVICE_ERROR",
        )
    try:
        data = response.json()
    except ValueError as error:
        raise RegistrationError("mail", "邮箱服务未返回有效 JSON", "MAIL_SERVICE_ERROR") from error
    if not isinstance(data, dict):
        raise RegistrationError("mail", "邮箱服务响应格式不正确", "MAIL_SERVICE_ERROR")
    return data


def refresh_access_token(record: MailboxRecord, session: Any, scope: str, timeout: float) -> str:
    data = _response_json(
        session.post(
            TOKEN_URL,
            data={
                "client_id": record.client_id,
                "refresh_token": record.refresh_token,
                "grant_type": "refresh_token",
                "scope": scope,
            },
            timeout=timeout,
        )
    )
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise RegistrationError("mail", "Microsoft OAuth 响应缺少 access_token", "MAIL_AUTH_FAILED")
    # 若服务器轮换 refresh token，仅更新本轮内存，不改写用户导入文件。
    refreshed = data.get("refresh_token")
    if isinstance(refreshed, str) and refreshed:
        record.refresh_token = refreshed
    return token


class ManualMailProvider:
    def __init__(self, link: str = "", prompt: Any = input):
        self.link = link
        self.prompt = prompt

    def wait_for_link(self, creation_id: str, timeout: float) -> str:
        try:
            link = self.link or self.prompt(
                f"收到 Steam 邮件后粘贴验证链接（creationid={creation_id}）："
            )
            return verification_url(link, creation_id)
        except (EOFError, OSError, ValueError) as error:
            raise RegistrationError(
                "mail", "未取得匹配本次会话的验证链接；请检查收件箱和垃圾箱", "MAIL_LINK_REQUIRED"
            ) from error

    def close(self) -> None:
        pass


class _PollingMailbox:
    def __init__(
        self, record: MailboxRecord, settings: Settings, *, clock=time.monotonic, sleep=time.sleep
    ):
        self.record = record
        self.settings = settings
        self.clock = clock
        self.sleep = sleep
        self.deadline = 0.0

    def request_timeout(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise RegistrationError("mail", "等待 Steam 验证邮件超时", "MAIL_TIMEOUT")
        return min(self.settings.mail_request_timeout, remaining)

    def messages(self) -> list[str]:
        raise NotImplementedError

    def wait_for_link(self, creation_id: str, timeout: float) -> str:
        self.deadline = self.clock() + timeout
        had_network_error = False
        while self.clock() < self.deadline:
            try:
                texts = self.messages()
            except (requests.RequestException, OSError, imaplib.IMAP4.abort) as error:
                self.reset_connection()
                had_network_error = True
                texts = []
                if isinstance(error, requests.HTTPError) and error.response is not None:
                    if error.response.status_code < 500 and error.response.status_code != 429:
                        raise RegistrationError(
                            "mail", "邮箱请求被拒绝，请检查邮箱服务配置", "MAIL_SERVICE_ERROR"
                        ) from error
            for text in texts:
                link = extract_verification_link(text, creation_id)
                if link:
                    return link
            self.sleep(min(self.settings.mail_poll_interval, max(0, self.deadline - self.clock())))
        message = (
            "邮箱服务在等待期间出现网络异常，未取得验证邮件"
            if had_network_error
            else ("等待 Steam 验证邮件超时，请检查收件箱、垃圾箱或邮箱权限")
        )
        raise RegistrationError("mail", message, "MAIL_TIMEOUT")

    def reset_connection(self) -> None:
        pass


class GraphMailProvider(_PollingMailbox):
    def __init__(self, record: MailboxRecord, settings: Settings, session: Any = None, **kwargs):
        super().__init__(record, settings, **kwargs)
        if not record.has_oauth:
            raise ValueError("Graph 邮箱需要四段记录中的 client_id 和 refresh_token")
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        self.access_token = ""

    def messages(self) -> list[str]:
        if not self.access_token:
            self.access_token = refresh_access_token(
                self.record, self.session, GRAPH_SCOPE, self.request_timeout()
            )
        url = "https://graph.microsoft.com/v1.0/me/messages"
        params = {
            "$top": self.settings.mail_scan_limit,
            "$orderby": "receivedDateTime desc",
            "$select": "id,body,bodyPreview,receivedDateTime",
        }
        response = self.session.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=self.request_timeout(),
        )
        if response.status_code == 401:
            self.access_token = refresh_access_token(
                self.record, self.session, GRAPH_SCOPE, self.request_timeout()
            )
            response = self.session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=self.request_timeout(),
            )
        data = _response_json(response)
        messages = data.get("value")
        if not isinstance(messages, list):
            raise RegistrationError("mail", "Graph 响应缺少邮件列表 value", "MAIL_SERVICE_ERROR")
        texts = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            body = message.get("body")
            if isinstance(body, dict) and isinstance(body.get("content"), str):
                texts.append(body["content"])
            if isinstance(message.get("bodyPreview"), str):
                texts.append(message["bodyPreview"])
        return texts

    def close(self) -> None:
        self.session.close()


class ImapMailProvider(_PollingMailbox):
    def __init__(self, record: MailboxRecord, settings: Settings, session: Any = None, **kwargs):
        super().__init__(record, settings, **kwargs)
        if not record.password and not record.has_oauth:
            raise ValueError("IMAP 邮箱需要邮箱密码或 OAuth 四段记录")
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        self.client: Any = None

    def messages(self) -> list[str]:
        try:
            if self.client is None:
                self.client = imaplib.IMAP4_SSL(
                    self.settings.imap_host, self.settings.imap_port, timeout=self.request_timeout()
                )
                if self.record.has_oauth:
                    token = refresh_access_token(
                        self.record, self.session, IMAP_SCOPE, self.request_timeout()
                    )
                    auth = f"user={self.record.email}\x01auth=Bearer {token}\x01\x01".encode()
                    self.client.authenticate("XOAUTH2", lambda _: auth)
                else:
                    self.client.login(self.record.email, self.record.password)
            texts = []
            selected = False
            for folder in self.settings.imap_folders:
                self.client.sock.settimeout(self.request_timeout())
                status, _ = self.client.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    continue
                selected = True
                status, results = self.client.uid("search", None, "ALL")
                if status != "OK" or not results or not isinstance(results[0], bytes):
                    raise RegistrationError("mail", "IMAP 邮件列表查询失败", "MAIL_SERVICE_ERROR")
                for uid in reversed(results[0].split()[-self.settings.mail_scan_limit :]):
                    self.client.sock.settimeout(self.request_timeout())
                    status, messages = self.client.uid("fetch", uid, "(BODY.PEEK[])")
                    if status != "OK":
                        continue
                    for message in messages or []:
                        if isinstance(message, tuple) and isinstance(message[1], bytes):
                            texts.extend(mail_texts(message[1]))
            if not selected:
                raise RegistrationError(
                    "mail", "IMAP 配置中的文件夹均不可访问", "MAIL_SERVICE_ERROR"
                )
            return texts
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error as error:
            raise RegistrationError(
                "mail", "IMAP 登录或读取失败，请检查邮箱权限和凭据", "MAIL_AUTH_FAILED"
            ) from error

    def reset_connection(self) -> None:
        if self.client is not None:
            try:
                self.client.shutdown()
            except (OSError, imaplib.IMAP4.error):
                pass
            self.client = None

    def close(self) -> None:
        self.reset_connection()
        self.session.close()


class Pop3MailProvider(_PollingMailbox):
    def __init__(self, record: MailboxRecord, settings: Settings, **kwargs):
        super().__init__(record, settings, **kwargs)
        if not record.password or record.has_oauth:
            raise ValueError("POP3 模式使用邮箱密码；OAuth 四段邮箱请选择 graph 或 imap")

    def messages(self) -> list[str]:
        client = None
        try:
            client = poplib.POP3_SSL(
                self.settings.pop3_host, self.settings.pop3_port, timeout=self.request_timeout()
            )
            client.user(self.record.email)
            client.pass_(self.record.password)
            count, _ = client.stat()
            texts = []
            for index in range(count, max(0, count - self.settings.mail_scan_limit), -1):
                client.sock.settimeout(self.request_timeout())
                _, lines, _ = client.retr(index)
                texts.extend(mail_texts(b"\r\n".join(lines)))
            return texts
        except poplib.error_proto as error:
            raise RegistrationError(
                "mail", "POP3 登录或读取失败，请检查邮箱权限和凭据", "MAIL_AUTH_FAILED"
            ) from error
        finally:
            if client:
                # 不发送 DELE；结束连接不会删除邮件。
                client.close()

    def close(self) -> None:
        pass


def create_mail_provider(record: MailboxRecord, settings: Settings, *, link: str = ""):
    provider = settings.mail_provider
    if provider == "auto":
        provider = "graph" if record.has_oauth else ("imap" if record.password else "manual")
    if provider == "manual":
        return ManualMailProvider(link)
    if provider == "graph":
        return GraphMailProvider(record, settings)
    if provider == "imap":
        return ImapMailProvider(record, settings)
    if provider == "pop3":
        return Pop3MailProvider(record, settings)
    if provider == "corouter":
        return CorouterMailProvider(record, settings)
    raise ValueError(f"不支持的邮箱提供商：{provider}")
