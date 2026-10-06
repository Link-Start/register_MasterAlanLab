from urllib.parse import urlsplit

import pytest
from conftest import (
    CREATION_ID,
    JOIN_HTML,
    Clock,
    Mailbox,
    Response,
    Session,
    Solver,
    registration_responses,
)
from curl_cffi.requests.exceptions import RequestException

from captcha import CaptchaRunClient
from config import Settings
from http_client import SteamSession
from models import RegistrationError, RegistrationInput
from signup import SteamRegistrar, parse_signup_page


def run(responses, *, settings=None, mailbox=None, **kwargs):
    settings = settings or Settings()
    transport, solver = Session(responses), Solver()
    client = SteamSession(settings, transport)
    clock = Clock()
    registrar = SteamRegistrar(
        client, settings, mailbox or Mailbox(), solver, clock=clock, sleep=clock.sleep, **kwargs
    )
    result = registrar.register(RegistrationInput("user@example.com", "steam_user", "Abc123!xyz"))
    return result, transport, solver


def test_full_registration_sequence_and_form_fields():
    mailbox = Mailbox()
    result, session, solver = run(registration_responses(), mailbox=mailbox)
    assert result.success and result.steamid == "76561190000000000"
    assert [urlsplit(url).path for _, url, _ in session.calls] == [
        "/join/",
        "/join/refreshcaptcha",
        "/join/ajaxverifyemail",
        "/account/newaccountverification",
        "/join/ajaxcheckemailverified",
        "/join/checkavail/",
        "/join/checkpasswordavail/",
        "/join/createaccount/",
    ]
    assert mailbox.creation_ids == [CREATION_ID]
    assert solver.challenges[0].sitekey == "live-key"
    assert session.calls[2][2]["data"] == {
        "email": "user@example.com",
        "captchagid": "100",
        "captcha_text": "token-100",
        "elang": 6,
        "init_id": "13703141833975910562",
        "guest": "false",
    }
    assert session.calls[-1][2]["data"] == {
        "accountname": "steam_user",
        "password": "Abc123!xyz",
        "count": 4,
        "lt": "0",
        "creation_sessionid": CREATION_ID,
        "embedded_appid": 0,
        "guest": "false",
    }
    for method, _, kwargs in session.calls:
        if method == "POST":
            assert "json" not in kwargs
            assert kwargs["headers"]["Content-Type"].startswith("application/x-www-form-urlencoded")
    debug = repr(result.as_dict())
    assert "Abc123!xyz" not in debug and "token-100" not in debug and "deadbeef" not in debug


def test_steam_receives_matching_solver_gid_and_token():
    config = Settings(captcha_key="key")
    transport = Session(registration_responses())
    captcha_session = Session(
        [
            Response({"taskId": "task-one"}),
            Response({"status": "Success", "response": {"token": "solved-token", "gid": "999"}}),
        ]
    )
    solver = CaptchaRunClient(config, captcha_session)
    registrar = SteamRegistrar(SteamSession(config, transport), config, Mailbox(), solver)
    result = registrar.register(RegistrationInput("user@example.com", "steam_user", "Abc123!xyz"))
    assert result.success
    assert transport.calls[2][2]["data"]["captchagid"] == "999"
    assert transport.calls[2][2]["data"]["captcha_text"] == "solved-token"


def test_rejected_captcha_refreshes_gid_and_uses_fresh_token():
    responses = registration_responses()
    responses[2:3] = [
        Response({"success": 101}),
        Response({"gid": "101", "type": 3, "sitekey": "new-key"}),
        Response({"success": 1, "sessionid": CREATION_ID}),
    ]
    result, session, solver = run(responses)
    assert result.success
    assert [challenge.gid for challenge in solver.challenges] == ["100", "101"]
    assert session.calls[4][2]["data"]["captcha_text"] == "token-101"
    assert session.calls[-1][2]["data"]["count"] == 5


def test_email_rejection_stops_without_creating_or_retrying_captcha():
    responses = registration_responses()[:3]
    responses[2] = Response({"success": 17})
    result, session, solver = run(responses)
    assert not result.success and result.code == 17 and result.stage == "send_email"
    assert len(solver.challenges) == 1 and len(session.calls) == 3


def test_wrong_creation_id_is_never_requested():
    result, session, _ = run(
        registration_responses()[:3],
        mailbox=Mailbox(
            "https://store.steampowered.com/account/newaccountverification?stoken=deadbeef&creationid=1"
        ),
    )
    assert result.code == "INVALID_VERIFICATION_LINK"
    assert len(session.calls) == 3


def test_redirect_outside_steam_is_not_followed():
    responses = registration_responses()[:4]
    responses[3] = Response(status_code=302, headers={"Location": "https://example.org/collect"})
    result, session, _ = run(responses)
    assert result.code == "INVALID_REDIRECT"
    assert len(session.calls) == 4


def test_creation_waits_for_server_confirmation():
    responses = registration_responses()
    responses[4:5] = [Response({"success": 0}), Response({"success": 0}), Response({"success": 1})]
    result, session, _ = run(responses)
    assert result.success
    checks = [call for call in session.calls if "ajaxcheckemailverified" in call[1]]
    assert len(checks) == 3


def test_verification_timeout_prevents_creation():
    settings = Settings(verify_timeout=4, poll_interval=3)
    responses = registration_responses()[:4] + [Response({"success": 0}), Response({"success": 0})]
    result, session, _ = run(responses, settings=settings)
    assert result.code == "VERIFICATION_TIMEOUT" and len(session.calls) == 6
    assert session.calls[-1][2]["timeout"] == 1


@pytest.mark.parametrize("code", [27, 29, 42])
def test_expired_session_stops_before_name_check(code):
    responses = registration_responses()[:4] + [Response({"success": code})]
    result, session, _ = run(responses)
    assert result.stage == "check_email" and result.code == code
    assert len(session.calls) == 5


@pytest.mark.parametrize(
    "response",
    [
        RequestException("proxy credentials must not leak"),
        Response(ValueError("html")),
        Response([]),
        Response({"unexpected": True}),
        Response(status_code=503),
    ],
)
def test_uncertain_creation_is_not_retried_and_trace_is_redacted(response):
    responses = registration_responses()
    responses[-1] = response
    result, session, _ = run(responses)
    assert not result.success and result.code == "OUTCOME_UNKNOWN"
    assert len([call for call in session.calls if "createaccount" in call[1]]) == 1
    assert "credentials" not in repr(result.as_dict())


@pytest.mark.parametrize("value", [False, 0, "false", "true"])
def test_false_or_string_success_never_counts_as_registered(value):
    responses = registration_responses()
    responses[-1] = Response({"bSuccess": value})
    result, _, _ = run(responses)
    assert not result.success


def test_unavailable_name_prevents_password_and_create_requests():
    responses = registration_responses()[:6]
    responses[-1] = Response({"bAvailable": False, "rgSuggestions": ["other"]})
    result, session, _ = run(responses)
    assert result.code == "NAME_UNAVAILABLE" and len(session.calls) == 6


def test_automatic_name_collisions_reuse_email_verification_and_captcha():
    responses = registration_responses()
    responses[5:6] = [
        Response({"bAvailable": False}),
        Response({"bAvailable": False}),
        Response({"bAvailable": True}),
    ]
    names = iter(["XingHe7316", "LiuYun2048"])
    changed = []
    mailbox = Mailbox()
    result, session, solver = run(
        responses,
        mailbox=mailbox,
        account_name_factory=lambda: next(names),
        on_account_name_change=lambda fields: changed.append(fields.account_name),
    )
    assert result.success and result.account_name == "LiuYun2048"
    checks = [call for call in session.calls if urlsplit(call[1]).path == "/join/checkavail/"]
    assert [call[2]["data"]["accountname"] for call in checks] == [
        "steam_user",
        "XingHe7316",
        "LiuYun2048",
    ]
    assert {call[2]["data"]["creationid"] for call in checks} == {CREATION_ID}
    assert changed == ["XingHe7316", "LiuYun2048"]
    assert mailbox.creation_ids == [CREATION_ID] and len(solver.challenges) == 1
    assert session.calls[-1][2]["data"]["accountname"] == "LiuYun2048"


def test_automatic_name_retry_limit_stops_before_password_or_creation():
    responses = registration_responses()[:5] + [Response({"bAvailable": False}) for _ in range(3)]
    names = iter(["XingHe7316", "LiuYun2048"])
    result, session, solver = run(
        responses,
        settings=Settings(account_name_attempts=3),
        account_name_factory=lambda: next(names),
    )
    assert result.code == "NAME_UNAVAILABLE" and result.account_name == "LiuYun2048"
    assert len(solver.challenges) == 1
    assert (
        len([call for call in session.calls if urlsplit(call[1]).path == "/join/checkavail/"]) == 3
    )
    assert all(
        urlsplit(call[1]).path not in {"/join/checkpasswordavail/", "/join/createaccount/"}
        for call in session.calls
    )


@pytest.mark.parametrize(
    "data", [{}, {"bAvailable": "false"}, {"bAvailable": 2}, {"bAvailable": 0.0}]
)
def test_invalid_name_check_response_does_not_generate_more_names(data):
    responses = registration_responses()[:6]
    responses[-1] = Response(data)

    def unexpected():
        raise AssertionError("Malformed response must not trigger another name")

    result, session, _ = run(responses, account_name_factory=unexpected)
    assert result.code == "INVALID_RESPONSE" and len(session.calls) == 6


def test_page_changes_fail_instead_of_fabricating_init_id():
    with pytest.raises(RegistrationError, match="init_id"):
        parse_signup_page("<html>blocked</html>")
    page = parse_signup_page(JOIN_HTML)
    assert page.init_id == "13703141833975910562" and not page.guest


def test_probe_only_reads_join_and_refreshes_captcha():
    transport = Session(registration_responses()[:2])
    data = SteamRegistrar(SteamSession(Settings(), transport), Settings()).probe()
    assert data["sitekey"] == "live-key" and data["captcha_gid"] == "100"
    assert len(transport.calls) == 2
