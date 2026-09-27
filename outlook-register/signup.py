from __future__ import annotations

from typing import Any
from urllib.parse import quote

from captcha import CaptchaRunClient
from config import Settings
from fingerprint import BrowserFingerprint, build_fingerprint, random_birth_date
from http_client import BrowserSession
from models import DEFAULT_CLIENT_ID, RegistrationInput, RegistrationResult, SignupPageContext
from output import extract_tokens
from risk import RiskProtocol, extract_hidden

SIGNUP_URL = "https://signup.live.com/?lic=1"
EXPERIMENT_URL = "https://signup.live.com/API/EvaluateExperimentAssignments"
AVAILABLE_URL = "https://signup.live.com/API/CheckAvailableSigninNames?lic=1"
CREATE_URL = "https://signup.live.com/API/CreateAccount?lic=1"


class OutlookSignupProtocol:
    """HTTP implementation of the recovered Task.ou_reg.oureg sequence."""

    def __init__(self, settings: Settings, *, session: BrowserSession | None = None):
        self.settings = settings
        self.signup_url = SIGNUP_URL + (f"&mkt={quote(settings.market)}" if settings.market else "")
        self.language = settings.market or "en-US"
        self.session = session or BrowserSession(
            proxy=settings.proxy,
            timeout=settings.timeout,
            user_agent=settings.user_agent,
            language=self.language,
        )
        self.fingerprint: BrowserFingerprint | None = None
        self.context: SignupPageContext | None = None
        self.trace: list[dict[str, Any]] = []

    def _page_headers(self) -> dict[str, str]:
        fp = self.fingerprint
        return {
            "Host": "signup.live.com",
            "Connection": "keep-alive",
            "sec-ch-ua": fp.sec_ch_ua if fp else '"Microsoft Edge";v="147", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": fp.sec_ch_ua_platform if fp else '"Windows"',
            "sec-ch-ua-platform-version": fp.sec_ch_ua_platform_version if fp else '"19.0.0"',
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": fp.user_agent if fp else self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Accept-Language": f"{self.language},{self.language.split('-')[0]};q=0.9",
        }

    def _api_headers(self, *, referer: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://signup.live.com",
            "Referer": referer or self.signup_url,
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "User-Agent": self.settings.user_agent,
        }
        if self.context and self.context.api_canary:
            headers["canary"] = self.context.api_canary
        if self.fingerprint:
            headers["client-request-id"] = self.fingerprint.uaid
        return headers

    def open_signup(self) -> SignupPageContext:
        self.fingerprint = build_fingerprint(
            user_agent=self.settings.user_agent,
            timezone=self.settings.timezone,
            language=self.language,
        )
        response = self.session.get(self.signup_url, headers=self._page_headers())
        html = response.text
        fp = self.fingerprint
        context = SignupPageContext(
            uaid=fp.uaid,
            api_canary=extract_hidden(html, "apiCanary"),
            unauth_session_id=extract_hidden(html, "sUnauthSessionID"),
            url_dfp=extract_hidden(html, "urlDfp") or fp.url_dfp,
            hip_fid=extract_hidden(html, "sHipFid"),
            hpgid=extract_hidden(html, "hpgid"),
            scenario_id=extract_hidden(html, "iScenarioId"),
            ui_flavor=extract_hidden(html, "iUiFlavor"),
            pref_sms_country=extract_hidden(html, "sPrefSMSCountry"),
            continuation_token=extract_hidden(html, "continuationToken"),
            txn_id=extract_hidden(html, "txnId"),
            rid=extract_hidden(html, "rid"),
            ticks=extract_hidden(html, "ticks"),
            auth_key=extract_hidden(html, "authKey"),
            cid=extract_hidden(html, "cid"),
            raw_html=html,
        )
        self.context = context
        self.trace.append({"step": "open_signup", "status": response.status_code, "url": response.url})
        return context

    def evaluate_experiments(self) -> dict[str, Any]:
        payload = {
            "clientExperiments": [
                {
                    "parallax": "enablesisufeedback",
                    "control": "enablesisufeedback_control",
                    "treatments": ["enablesisufeedback_treatment"],
                },
                {
                    "parallax": "addprivatebrowsingtexttofabricfooter",
                    "control": "addprivatebrowsingtexttofabricfooter_control",
                    "treatments": ["addprivatebrowsingtexttofabricfooter_treatment"],
                },
            ]
        }
        response = self.session.post(EXPERIMENT_URL, headers=self._api_headers(), json=payload)
        data = self.session.json(response)
        self.trace.append({"step": "EvaluateExperimentAssignments", "status": response.status_code})
        return data

    def check_available(self, registration: RegistrationInput) -> dict[str, Any]:
        if not self.context or not self.fingerprint:
            raise RuntimeError("open_signup must run before check_available")
        payload = {
            "includeSuggestions": True,
            "signInName": registration.member_name,
            "uiflvr": 1001,
            "scid": 100118,
            "uaid": self.fingerprint.uaid,
            "hpgid": 200225,
        }
        response = self.session.post(AVAILABLE_URL, headers=self._api_headers(), json=payload)
        data = self.session.json(response)
        self.trace.append({"step": "CheckAvailableSigninNames", "status": response.status_code, "data": data})
        return data

    def _risk_step(self, registration: RegistrationInput) -> dict[str, Any]:
        assert self.context is not None and self.fingerprint is not None
        risk = RiskProtocol(self.session, signup_referer=self.signup_url)
        clear_data = risk.clear_html(uaid=self.fingerprint.uaid, fingerprint=self.fingerprint)
        init_data = risk.initialize(self.context, self.fingerprint)
        self.trace.append({"step": "risk.initialize", "data": init_data})

        token = registration.captcha_token
        if not token and self.settings.captcha_run_key:
            captcha = CaptchaRunClient(
                self.session,
                api_key=self.settings.captcha_run_key,
                base_url=self.settings.captcha_run_base_url,
                poll_interval=self.settings.captcha_poll_interval,
                max_wait=self.settings.captcha_max_wait,
            )
            solution = captcha.solve(
                uaid=self.fingerprint.uaid,
                country=registration.country,
                timezone=self.settings.timezone,
                proxy=registration.proxy or self.settings.proxy,
            )
            token = solution.token
        if not token:
            raise RuntimeError("captcha token is required; pass --captcha-token or configure CAPTCHA_RUN_KEY")

        verify_data = risk.verify(self.context, registration, self.fingerprint, token)
        self.trace.append({"step": "risk.verify", "data": verify_data})
        return {"clear": clear_data, "initialize": init_data, "verify": verify_data}

    def create_account(self, registration: RegistrationInput, *, risk_data: dict[str, Any]) -> RegistrationResult:
        if not self.context or not self.fingerprint:
            raise RuntimeError("open_signup must run before create_account")
        birth_date = registration.birth_date or random_birth_date()
        self.context.continuation_token = self.context.continuation_token or extract_hidden(
            self.context.raw_html, "continuationToken"
        )
        payload: dict[str, Any] = {
            "BirthDate": birth_date.strftime("%m:%d:%Y"),
            "Country": registration.country,
            "FirstName": registration.first_name,
            "LastName": registration.last_name,
            "MemberName": registration.member_name,
            "Password": registration.password,
            "ReturnUrl": "https://login.live.com/oauth20_desktop.srf",
            "SignupReturnUrl": self.signup_url,
            "SuggestedAccountType": "EASI",
            "SiteId": "00000000487A244A",
            "VerificationCodeSlt": self.context.verification_code_slt,
            "PrivateAccessToken": self.context.private_access_token,
            "WReply": self.context.wreply,
            "MemberNameChangeCount": 0,
            "MemberNameAvailableCount": 1,
            "MemberNameUnavailableCount": 0,
            "IsUserConsentedToChinaPIPL": False,
            "ContinuationToken": self.context.continuation_token,
            "uaid": self.fingerprint.uaid,
            "hpgid": 200225,
            "correlationId": self.fingerprint.uaid,
            "riskAssessmentDetails": risk_data.get("verify", {}).get("riskAssessmentDetails", {}),
            "repMapRequestIdentifierDetails": risk_data.get("verify", {}).get("repMapRequestIdentifierDetails", {}),
            "RiskAssessmentDetails": risk_data.get("verify", {}).get("RiskAssessmentDetails", {}),
            "RepMapRequestIdentifierDetails": risk_data.get("verify", {}).get("RepMapRequestIdentifierDetails", {}),
            "arkoseBlob": self.context.arkose_blob,
        }
        response = self.session.post(CREATE_URL, headers=self._api_headers(), json=payload)
        data = self.session.json(response)
        self.trace.append({"step": "CreateAccount", "status": response.status_code, "data": data})
        refresh_token, access_token = extract_tokens(data)
        code = data.get("code")
        redirect_url = data.get("redirectUrl") or response.headers.get("Location")
        success = response.ok and not data.get("error") and str(code) not in {"1041", "1059"}
        message = "注册成功" if success else str(data.get("errorData") or data.get("message") or data)
        return RegistrationResult(
            success=success,
            member_name=registration.member_name,
            password=registration.password,
            client_id=DEFAULT_CLIENT_ID,
            refresh_token=refresh_token,
            access_token=access_token,
            status_code=response.status_code,
            code=code,
            redirect_url=redirect_url,
            message=message,
            response=data,
        )

    def register(self, registration: RegistrationInput, *, execute: bool = True) -> RegistrationResult:
        """Run the same high-level sequence as oureg.__enter__ / CreateAccount."""
        self.open_signup()
        experiments = self.evaluate_experiments()
        if experiments.get("error"):
            return RegistrationResult(
                False,
                registration.member_name,
                message="实验分配接口返回错误",
                response={"step": "EvaluateExperimentAssignments", "data": experiments},
            )
        available = self.check_available(registration)
        if available.get("error") or not isinstance(available.get("isAvailable"), bool):
            return RegistrationResult(
                False,
                registration.member_name,
                message="用户名检测接口未返回有效结果",
                response={"step": "CheckAvailableSigninNames", "data": available},
            )
        if available and available.get("isAvailable") is False:
            return RegistrationResult(False, registration.member_name, message="用户名不可用", response=available)
        if not execute:
            return RegistrationResult(
                True,
                registration.member_name,
                password=registration.password,
                message="dry-run: signup requests prepared",
                dry_run=True,
                response={"trace": self.trace, "available": available},
            )
        risk_data = self._risk_step(registration)
        return self.create_account(registration, risk_data=risk_data)
