import pytest
import requests
from conftest import Clock, Response, Session

from captcha import CaptchaRunClient, ManualCaptchaSolver
from config import Settings
from models import CaptchaChallenge, RegistrationError


def test_recovered_hcaptcha_task_and_result_contract():
    clock = Clock()
    session = Session(
        [
            Response({"taskId": "task/1"}),
            Response({"status": "Working"}),
            Response({"status": "Success", "response": {"captcha_key": "answer"}}),
        ]
    )
    client = CaptchaRunClient(
        Settings(captcha_key="own-key"), session, clock=clock, sleep=clock.sleep
    )
    assert client.solve(CaptchaChallenge("gid", sitekey="current-key")) == "answer"
    assert session.calls[0][2]["json"] == {
        "captchaType": "HCaptchaSteam",
        "siteReferer": "https://store.steampowered.com/join/",
        "siteKey": "current-key",
        "developer": "542f4f4f-31b6-4b70-b485-c4762c45d1e8",
    }
    assert session.calls[1][1].endswith("/task%2F1")
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer own-key"
    assert not session.trust_env and all("proxies" not in call[2] for call in session.calls)


@pytest.mark.parametrize(
    "result",
    [
        {"status": "Fail"},
        {"status": "Unexpected"},
        {"status": "Success", "response": {"captcha_key": ""}},
    ],
)
def test_invalid_captcha_result_cannot_supply_empty_token(result):
    session = Session([Response({"taskId": 1}), Response(result)])
    with pytest.raises(RegistrationError):
        CaptchaRunClient(Settings(captcha_key="key"), session).solve(
            CaptchaChallenge("gid", sitekey="key")
        )


def test_captcha_deadline_bounds_polling():
    clock = Clock()
    settings = Settings(captcha_key="key", captcha_max_wait=5, captcha_poll_interval=3)
    session = Session(
        [Response({"taskId": 1}), Response({"status": "Working"}), Response({"status": "Working"})]
    )
    with pytest.raises(RegistrationError) as caught:
        CaptchaRunClient(settings, session, clock=clock, sleep=clock.sleep).solve(
            CaptchaChallenge("gid", sitekey="key")
        )
    assert caught.value.code == "CAPTCHA_TIMEOUT" and clock.now == 5
    assert session.calls[-1][2]["timeout"] == 2


def test_no_captcha_does_not_create_paid_task():
    session = Session([])
    assert CaptchaRunClient(Settings(), session).solve(CaptchaChallenge("-1")) == ""
    assert not session.calls


def test_manual_token_is_consumed_once():
    prompts = []
    solver = ManualCaptchaSolver("first", prompt=lambda message: prompts.append(message) or "fresh")
    assert solver.solve(CaptchaChallenge("1")) == "first"
    assert solver.solve(CaptchaChallenge("2")) == "fresh"
    assert len(prompts) == 1


@pytest.mark.parametrize(
    ("response", "expected_gid"),
    [
        ({"token": " answer "}, "original-gid"),
        ({"token": " answer ", "gid": "123456789"}, "123456789"),
        ({"token": " answer ", "gid": 123456789}, "123456789"),
    ],
)
def test_current_steam_result_uses_returned_token_and_gid(response, expected_gid):
    session = Session(
        [
            Response({"taskId": "task-one"}),
            Response({"status": "Success", "response": response}),
        ]
    )
    challenge = CaptchaChallenge("original-gid", sitekey="current-key")
    assert CaptchaRunClient(Settings(captcha_key="key"), session).solve(challenge) == "answer"
    assert challenge.gid == expected_gid


@pytest.mark.parametrize("gid", [True, "", [], {}])
def test_invalid_solver_gid_cannot_be_submitted(gid):
    session = Session(
        [
            Response({"taskId": "task-one"}),
            Response({"status": "Success", "response": {"token": "answer", "gid": gid}}),
        ]
    )
    with pytest.raises(RegistrationError) as caught:
        CaptchaRunClient(Settings(captcha_key="key"), session).solve(
            CaptchaChallenge("original", sitekey="current-key")
        )
    assert caught.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize(
    "failure", [Response(status_code=502), requests.ReadTimeout("temporary network error")]
)
def test_poll_transient_error_retries_existing_task_without_creating_another(failure):
    clock = Clock()
    session = Session(
        [
            Response({"taskId": "task-one"}),
            failure,
            Response({"status": "Success", "response": {"token": "answer", "gid": "123"}}),
        ]
    )
    client = CaptchaRunClient(Settings(captcha_key="key"), session, clock=clock, sleep=clock.sleep)
    assert client.solve(CaptchaChallenge("original", sitekey="key")) == "answer"
    assert len([call for call in session.calls if call[0] == "POST"]) == 1
    assert session.calls[1][1] == session.calls[2][1] and clock.now == 3


def test_task_creation_timeout_does_not_resubmit_paid_task():
    session = Session([requests.ReadTimeout("unknown creation result")])
    with pytest.raises(RegistrationError):
        CaptchaRunClient(Settings(captcha_key="key"), session).solve(
            CaptchaChallenge("original", sitekey="key")
        )
    assert len(session.calls) == 1 and session.calls[0][0] == "POST"
