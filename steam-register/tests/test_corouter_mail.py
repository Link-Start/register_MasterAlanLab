import pytest
import requests
from conftest import CREATION_ID, LINK, Clock, Response, Session

import corouter_mail_provider as corouter
from config import Settings
from corouter_mail_provider import (
    CorouterMailClient,
    CorouterMailProvider,
    load_corouter_mailboxes,
)
from models import MailboxRecord, RegistrationError


def settings(**kwargs):
    return Settings(corouter_tenant_id="tenant/one", corouter_api_key="test-secret", **kwargs)


def envelope(data):
    return Response({"code": 0, "data": data, "message": "ok"})


def mail_list(items):
    return envelope({"items": items, "channel": "graph"})


def account(email, account_id, status="active"):
    return {"id": account_id, "email": email, "status": status}


@pytest.mark.parametrize(
    "key", ["test-secret", "Bearer test-secret", "Authorization: Bearer test-secret"]
)
def test_api_key_formats_and_request_settings(key):
    config = settings()
    config.corouter_api_key = key
    session = Session([envelope([])])
    client = CorouterMailClient(config, session)
    assert client.request_json("groups") == []
    method, url, options = session.calls[0]
    assert method == "GET" and "/tenants/tenant%2Fone/mail/groups" in url
    assert options["headers"]["Authorization"] == "Bearer test-secret"
    assert options["timeout"] == 90 and options["allow_redirects"] is False
    assert not session.trust_env and "test-secret" not in repr(config)
    client.close()
    assert session.closed


def test_account_discovery_resolves_group_paginates_and_deduplicates():
    session = Session(
        [
            envelope([{"id": "group-id", "name": "Steam"}]),
            envelope(
                {
                    "items": [
                        account("User@example.com", "first"),
                        account("disabled@example.com", "disabled", "disabled"),
                    ],
                    "pagination": {"pages": 2},
                }
            ),
            envelope(
                {
                    "items": [
                        account("user@example.com", "duplicate"),
                        account("other@example.com", "second"),
                    ]
                }
            ),
        ]
    )
    config = settings(corouter_group_id="steam")
    with CorouterMailClient(config, session) as client:
        records = client.list_mailboxes()
    assert [(record.email, record.account_id) for record in records] == [
        ("User@example.com", "first"),
        ("other@example.com", "second"),
    ]
    for index, page in [(1, 1), (2, 2)]:
        assert session.calls[index][2]["params"] == {
            "page": page,
            "limit": 200,
            "status": "active",
            "group_id": "group-id",
        }
    assert session.closed


def test_explicit_address_lookup_matches_exactly_and_closes_discovery_session(monkeypatch):
    session = Session(
        [
            envelope(
                {
                    "items": [
                        account("otheruser@example.com", "other"),
                        account("USER@example.com", "correct"),
                    ],
                    "pagination": {"pages": 1},
                }
            )
        ]
    )
    monkeypatch.setattr(corouter.requests, "Session", lambda: session)
    records = load_corouter_mailboxes(settings(), [MailboxRecord("user@example.com")])
    assert records[0].account_id == "correct" and session.closed
    assert session.calls[0][2]["params"]["q"] == "user@example.com"


def test_provider_resolves_address_before_registration():
    session = Session([envelope({"items": [account("USER@example.com", "correct")]})])
    provider = CorouterMailProvider(MailboxRecord("user@example.com"), settings(), session)
    assert provider.account_id == "correct"
    provider.close()
    assert session.closed


def test_unknown_explicit_mailbox_is_not_replaced_by_another_account(monkeypatch):
    session = Session([envelope({"items": [account("otheruser@example.com", "wrong")]})])
    monkeypatch.setattr(corouter.requests, "Session", lambda: session)
    with pytest.raises(RegistrationError) as caught:
        load_corouter_mailboxes(settings(), [MailboxRecord("user@example.com")])
    assert caught.value.code == "MAIL_NOT_FOUND" and session.closed


def test_skipped_addresses_do_not_need_mail_api_credentials():
    assert load_corouter_mailboxes(Settings(), []) == []


def test_preview_link_avoids_detail_request_and_caps_top():
    session = Session([mail_list([{"id": "one", "folder": "junk", "body_preview": LINK}])])
    clock = Clock()
    provider = CorouterMailProvider(
        MailboxRecord("user@example.com", account_id="account/one"),
        settings(mail_scan_limit=100),
        session,
        clock=clock,
        sleep=clock.sleep,
    )
    assert provider.wait_for_link(CREATION_ID, 300) == LINK
    assert len(session.calls) == 1 and "account%2Fone/messages" in session.calls[0][1]
    assert session.calls[0][2]["params"] == {"folder": "all", "top": 50, "skip": 0}
    assert session.calls[0][2]["timeout"] == 90


def test_detail_cache_is_scoped_by_folder_and_id_mode():
    junk = {
        "id": "message/one",
        "folder": "junkemail",
        "id_mode": "immutable",
        "subject": "Steam verification",
    }
    inbox = {**junk, "folder": "inbox"}
    alternate_mode = {**junk, "id_mode": "rest"}
    session = Session(
        [
            mail_list([junk]),
            envelope({"body": LINK.replace(CREATION_ID, "2")}),
            mail_list([junk, inbox]),
            envelope({"body": LINK.replace(CREATION_ID, "3")}),
            mail_list([junk, inbox, alternate_mode]),
            envelope({"body": f'<a href="{LINK.replace("&", "&amp;")}">Verify</a>'}),
        ]
    )
    clock = Clock()
    provider = CorouterMailProvider(
        MailboxRecord("user@example.com", account_id="one"),
        settings(),
        session,
        clock=clock,
        sleep=clock.sleep,
    )
    assert provider.wait_for_link(CREATION_ID, 30) == LINK and clock.now == 10
    details = [call for call in session.calls if "message%2Fone" in call[1]]
    assert [call[2]["params"] for call in details] == [
        {"folder": "junkemail", "id_mode": "immutable"},
        {"folder": "inbox", "id_mode": "immutable"},
        {"folder": "junkemail", "id_mode": "rest"},
    ]


def test_transient_detail_failure_does_not_cache_the_message():
    message = {"id": "one", "folder": "junk", "id_mode": "rest", "subject": "Steam"}
    session = Session(
        [
            mail_list([message]),
            Response(status_code=502),
            mail_list([message]),
            envelope({"body": LINK}),
        ]
    )
    clock = Clock()
    provider = CorouterMailProvider(
        MailboxRecord("user@example.com", account_id="one"),
        settings(),
        session,
        clock=clock,
        sleep=clock.sleep,
    )
    assert provider.wait_for_link(CREATION_ID, 20) == LINK and clock.now == 5
    assert len([call for call in session.calls if call[1].endswith("/messages/one")]) == 2


def test_recovered_empty_inbox_keeps_waiting_and_unrelated_mail_uses_no_detail_quota():
    session = Session(
        [
            requests.ConnectionError("transport contained test-secret"),
            mail_list([{"id": "newsletter", "subject": "Newsletter", "from": "news@example.com"}]),
            mail_list([{"body_preview": LINK}]),
        ]
    )
    clock = Clock()
    provider = CorouterMailProvider(
        MailboxRecord("user@example.com", account_id="one"),
        settings(),
        session,
        clock=clock,
        sleep=clock.sleep,
    )
    assert provider.wait_for_link(CREATION_ID, 20) == LINK and clock.now == 10
    assert len(session.calls) == 3


def test_repeated_rate_limits_respect_mail_deadline():
    session = Session([Response(status_code=429), Response(status_code=429)])
    clock = Clock()
    provider = CorouterMailProvider(
        MailboxRecord("user@example.com", account_id="one"),
        settings(),
        session,
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(RegistrationError) as caught:
        provider.wait_for_link(CREATION_ID, 12)
    assert caught.value.code == "MAIL_TIMEOUT" and clock.now == 12
    assert [call[2]["timeout"] for call in session.calls] == [12, 7]


@pytest.mark.parametrize(
    ("status", "payload", "code"),
    [
        (401, None, "MAIL_AUTH_FAILED"),
        (403, {"code": 1001, "message": "test-secret"}, "MAIL_QUOTA_EXCEEDED"),
        (403, {"code": 1000, "message": "test-secret"}, "MAIL_ACCESS_DENIED"),
        (404, None, "MAIL_NOT_FOUND"),
        (409, {"data": {"error_kind": "account_unavailable"}}, "MAIL_ACCOUNT_UNAVAILABLE"),
        (200, {"code": 7, "message": "test-secret"}, "MAIL_SERVICE_ERROR"),
        (200, {"code": False, "data": {}}, "MAIL_SERVICE_ERROR"),
    ],
)
def test_permanent_errors_stop_polling_without_echoing_payload(status, payload, code):
    clock = Clock()
    session = Session([Response(payload, status_code=status)])
    provider = CorouterMailProvider(
        MailboxRecord("user@example.com", account_id="one"),
        settings(),
        session,
        clock=clock,
        sleep=clock.sleep,
    )
    with pytest.raises(RegistrationError) as caught:
        provider.wait_for_link(CREATION_ID, 20)
    assert caught.value.code == code and clock.now == 0
    assert len(session.calls) == 1 and "test-secret" not in str(caught.value)
