from email.message import EmailMessage

import pytest
from conftest import CREATION_ID, LINK, Clock, Response, Session

import mail_provider
from config import Settings
from mail_provider import (
    GraphMailProvider,
    ImapMailProvider,
    Pop3MailProvider,
    load_mailboxes,
    parse_mailbox,
)
from models import MailboxRecord, RegistrationError
from utils import extract_verification_link, mail_texts, verification_url

CLIENT = "12345678-1234-1234-1234-123456789abc"


@pytest.mark.parametrize("separator", ["----", "|", "\t"])
@pytest.mark.parametrize("reverse", [False, True])
def test_four_field_import_compatibility(separator, reverse):
    fields = ["user@outlook.com", "mail-password", CLIENT, "refresh-secret"]
    if reverse:
        fields[2:] = reversed(fields[2:])
    record = parse_mailbox(separator.join(fields))
    assert record.email == "user@outlook.com" and record.client_id == CLIENT
    assert record.refresh_token == "refresh-secret" and record.password == "mail-password"
    assert "secret" not in repr(record) and "password" not in repr(record)


def test_import_preserves_password_and_deduplicates_case_insensitively(tmp_path):
    path = tmp_path / "mail.txt"
    path.write_text(
        "\ufeff# comment\nUser@example.com---- Password with spaces \nuser@example.com----second\n",
        encoding="utf-8",
    )
    records = load_mailboxes(path)
    assert len(records) == 1 and records[0].password == " Password with spaces "


def test_import_failure_does_not_echo_credentials(tmp_path):
    path = tmp_path / "mail.txt"
    path.write_text("user@example.com----secret-password----invalid----secret-refresh")
    with pytest.raises(ValueError) as caught:
        load_mailboxes(path)
    assert "第 1 行" in str(caught.value) and "secret" not in str(caught.value)


@pytest.mark.parametrize("body", [LINK, f'<a href="{LINK.replace("&", "&amp;")}">确认邮箱</a>'])
def test_verification_link_extraction(body):
    assert extract_verification_link(body, CREATION_ID) == LINK


@pytest.mark.parametrize(
    "url",
    [
        LINK.replace("https://", "http://"),
        LINK.replace(".com/", ".com.evil.test/"),
        LINK.replace(".com/", ".com:444/"),
        LINK.replace(".com/", ".com@evil.test/"),
        LINK.replace(CREATION_ID, "1"),
        LINK + "&creationid=1",
        LINK + "#fragment",
    ],
)
def test_verification_url_rejects_other_hosts_and_sessions(url):
    with pytest.raises(ValueError):
        verification_url(url, CREATION_ID)


@pytest.mark.parametrize("encoding", ["quoted-printable", "base64"])
def test_mime_html_is_decoded_before_link_extraction(encoding):
    message = EmailMessage()
    message.set_content(
        f'<a href="{LINK.replace("&", "&amp;")}">确认</a>',
        subtype="html",
        charset="utf-8",
        cte=encoding,
    )
    assert extract_verification_link("\n".join(mail_texts(message.as_bytes())), CREATION_ID) == LINK


def test_graph_polls_recent_mail_until_matching_session_arrives():
    clock = Clock()
    session = Session(
        [
            Response({"access_token": "access", "refresh_token": "rotated"}),
            Response({"value": [{"body": {"content": LINK.replace(CREATION_ID, "2")}}]}),
            Response({"value": [{"body": {"content": LINK.replace("&", "&amp;")}}]}),
        ]
    )
    record = MailboxRecord("user@outlook.com", client_id=CLIENT, refresh_token="refresh")
    provider = GraphMailProvider(record, Settings(), session, clock=clock, sleep=clock.sleep)
    assert provider.wait_for_link(CREATION_ID, 30) == LINK
    assert not session.trust_env and record.refresh_token == "rotated"
    assert session.calls[0][2]["data"]["scope"] == "https://graph.microsoft.com/.default"
    assert session.calls[1][2]["params"]["$top"] == 20
    assert all("proxies" not in call[2] for call in session.calls)


def test_graph_auth_failure_stops_polling_immediately():
    record = MailboxRecord("user@outlook.com", client_id=CLIENT, refresh_token="secret")
    session = Session([Response({"error": "invalid_grant"}, status_code=400)])
    with pytest.raises(RegistrationError) as caught:
        GraphMailProvider(record, Settings(), session).wait_for_link(CREATION_ID, 30)
    assert len(session.calls) == 1 and "secret" not in str(caught.value)


def test_graph_transient_error_retries_within_mail_deadline():
    clock = Clock()
    record = MailboxRecord("user@outlook.com", client_id=CLIENT, refresh_token="secret")
    session = Session(
        [
            Response(status_code=502),
            Response({"access_token": "access"}),
            Response({"value": [{"bodyPreview": LINK}]}),
        ]
    )
    provider = GraphMailProvider(record, Settings(), session, clock=clock, sleep=clock.sleep)
    assert provider.wait_for_link(CREATION_ID, 30) == LINK and clock.now == 5


def test_imap_reads_junk_without_marking_mail_read(monkeypatch):
    calls = []
    message = EmailMessage()
    message.set_content(LINK)

    class Imap:
        sock = type("Socket", (), {"settimeout": lambda *args: None})()

        def login(self, user, password):
            calls.append(("login", user, password))

        def select(self, folder, readonly):
            calls.append(("select", folder, readonly))
            self.folder = folder
            return "OK", []

        def uid(self, command, *args):
            calls.append((command, *args))
            if command == "search":
                return "OK", [b"1" if self.folder == '"Junk"' else b""]
            return "OK", [(b"info", message.as_bytes())]

        def shutdown(self):
            calls.append(("shutdown",))

    monkeypatch.setattr(mail_provider.imaplib, "IMAP4_SSL", lambda *args, **kwargs: Imap())
    provider = ImapMailProvider(
        MailboxRecord("user@example.com", "mailpass"), Settings(imap_folders=("INBOX", "Junk"))
    )
    assert provider.wait_for_link(CREATION_ID, 10) == LINK
    assert ("select", '"INBOX"', True) in calls and ("select", '"Junk"', True) in calls
    assert ("fetch", b"1", "(BODY.PEEK[])") in calls
    provider.close()


def test_pop3_reads_recent_message_without_deleting(monkeypatch):
    calls = []
    message = EmailMessage()
    message.set_content(LINK)

    class Pop:
        sock = type("Socket", (), {"settimeout": lambda *args: None})()

        def user(self, value):
            calls.append("user")

        def pass_(self, value):
            calls.append("pass")

        def stat(self):
            return 1, 100

        def retr(self, index):
            calls.append("retr")
            return b"+OK", message.as_bytes().splitlines(), 100

        def close(self):
            calls.append("close")

    monkeypatch.setattr(mail_provider.poplib, "POP3_SSL", lambda *args, **kwargs: Pop())
    provider = Pop3MailProvider(MailboxRecord("user@example.com", "mailpass"), Settings())
    assert provider.wait_for_link(CREATION_ID, 10) == LINK
    assert calls == ["user", "pass", "retr", "close"]
