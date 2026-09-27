from __future__ import annotations

from dataclasses import dataclass

from config import Settings
from models import RegistrationInput
from signup import OutlookSignupProtocol


@dataclass
class FakeResponse:
    payload: dict
    status_code: int = 200
    text: str = ""
    url: str = "https://signup.live.com/?lic=1"
    headers: dict[str, str] | None = None
    ok: bool = True

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.responses = [
            FakeResponse(
                {},
                text='{"apiCanary":"CANARY","sUnauthSessionID":"SID","urlDfp":"DFP","hpgid":1,"iScenarioId":2,"iUiFlavor":3,"txnId":"TX","rid":"RID","ticks":"1","authKey":"AK","cid":"CID","continuationToken":"CONT"}',
            ),
            FakeResponse({"ok": True}),
            FakeResponse({"isAvailable": True}),
            FakeResponse({"cleared": True}),
            FakeResponse({"risk": "initialized", "continuationToken": "RISK_CONT"}),
            FakeResponse({"risk": "verified", "RiskAssessmentDetails": {"ok": True}}),
            FakeResponse({"code": 0, "redirectUrl": "https://login.live.com/"}),
        ]

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


class SessionAdapter:
    @staticmethod
    def json(response):
        return response.json()


def test_reconstructed_registration_order():
    fake = FakeSession()
    protocol = OutlookSignupProtocol(Settings(), session=fake)
    protocol.session = fake
    protocol.session.json = SessionAdapter.json
    item = RegistrationInput(member_name="demo@example.com", password="Password123!", captcha_token="CAPTCHA")
    result = protocol.register(item, execute=True)

    assert result.success
    assert [method for method, _, _ in fake.calls] == ["GET", "POST", "POST", "GET", "POST", "POST", "POST"]
    assert fake.calls[0][1] == "https://signup.live.com/?lic=1"
    assert fake.calls[1][1].endswith("EvaluateExperimentAssignments")
    assert fake.calls[2][1].endswith("CheckAvailableSigninNames?lic=1")
    assert fake.calls[3][1].endswith("Clear.HTML")
    assert fake.calls[4][1].endswith("/risk/initialize")
    assert fake.calls[5][1].endswith("/risk/verify")
    assert fake.calls[6][1].endswith("CreateAccount?lic=1")
    create_payload = fake.calls[6][2]["json"]
    assert create_payload["MemberName"] == item.member_name
    assert create_payload["SiteId"] == "00000000487A244A"
    assert create_payload["ContinuationToken"] == "RISK_CONT"


def test_username_api_error_is_not_reported_as_dry_run_success():
    fake = FakeSession()
    fake.responses[2] = FakeResponse({"error": {"code": "1181"}})
    protocol = OutlookSignupProtocol(Settings(), session=fake)
    protocol.session.json = SessionAdapter.json
    item = RegistrationInput(member_name="demo@outlook.jp", password="Password123!")

    result = protocol.register(item, execute=False)

    assert not result.success
    assert result.response["step"] == "CheckAvailableSigninNames"
    assert len(fake.calls) == 3


def test_japanese_market_is_used_for_signup_request():
    fake = FakeSession()
    protocol = OutlookSignupProtocol(Settings(country="JP", market="ja-JP", timezone=540), session=fake)
    protocol.session.json = SessionAdapter.json
    item = RegistrationInput(member_name="demo@outlook.jp", password="Password123!", country="JP")

    result = protocol.register(item, execute=False)

    assert result.dry_run
    assert fake.calls[0][1] == "https://signup.live.com/?lic=1&mkt=ja-JP"
    assert fake.calls[0][2]["headers"]["Accept-Language"] == "ja-JP,ja;q=0.9"
