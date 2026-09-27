from __future__ import annotations

import json
import re
from typing import Any

from fingerprint import BrowserFingerprint
from http_client import BrowserSession
from models import RegistrationInput, SignupPageContext

RISK_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
RISK_BASE = f"https://login.microsoftonline.com/{RISK_TENANT}/api/v1.0/risk"
CLEAR_URL = "https://df.cfp.microsoft.com/Clear.HTML"


class RiskProtocol:
    """The FPT/risk requests used by oureg.login_initialize/login_verify."""

    def __init__(
        self,
        session: BrowserSession,
        *,
        signup_origin: str = "https://signup.live.com",
        signup_referer: str = "https://signup.live.com/?lic=1",
    ):
        self.session = session
        self.signup_origin = signup_origin
        self.signup_referer = signup_referer

    def clear_html(self, *, uaid: str, fingerprint: BrowserFingerprint) -> dict[str, Any]:
        params = {"correlationid": uaid, "ctx": "Ls1.0", "wl": "False"}
        headers = {
            "Origin": self.signup_origin,
            "Referer": self.signup_origin + "/",
            "Content-Type": "application/json",
        }
        response = self.session.get(CLEAR_URL, params=params, headers=headers)
        return self.session.json(response)

    def initialize(self, ctx: SignupPageContext, fingerprint: BrowserFingerprint) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Origin": self.signup_origin,
            "Referer": self.signup_referer,
            "client-request-id": ctx.uaid,
        }
        payload = {
            "continuationToken": ctx.continuation_token,
            "uaid": ctx.uaid,
            "fpt": fingerprint.url_dfp,
            "urlDfp": fingerprint.url_dfp,
            "isRdm": False,
            "siteId": "00000000487A244A",
            "uiFlavor": "Web",
            "appId": "Signup",
        }
        response = self.session.post(f"{RISK_BASE}/initialize", headers=headers, json=payload)
        data = self.session.json(response)
        if isinstance(data.get("continuationToken"), str):
            ctx.continuation_token = data["continuationToken"]
        if data:
            ctx.risk_required = True
        return data

    def verify(
        self,
        ctx: SignupPageContext,
        registration: RegistrationInput,
        fingerprint: BrowserFingerprint,
        token: str,
        *,
        px3: str = "",
        pxde: str = "",
        pxvid: str = "",
    ) -> dict[str, Any]:
        birth_date = registration.birth_date.isoformat() if registration.birth_date else ""
        payload = {
            "msaCreateSignature": ctx.api_canary,
            "memberName": registration.member_name,
            "siteId": "00000000487A244A",
            "uiFlavor": "Web",
            "appId": "Signup",
            "birthdate": birth_date,
            "firstName": registration.first_name,
            "lastName": registration.last_name,
            "countryCode": registration.country,
            "verificationCode": token,
            "deviceDetails": {"isRdm": False, "uaid": fingerprint.uaid, "urlDfp": fingerprint.url_dfp},
            "challengeSolution": token,
            "challengeType": registration.captcha_type,
            "px3": px3,
            "pxde": pxde,
            "pxvid": pxvid,
        }
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Origin": self.signup_origin,
            "Referer": self.signup_referer,
            "client-request-id": fingerprint.uaid,
        }
        response = self.session.post(f"{RISK_BASE}/verify", headers=headers, json=payload)
        return self.session.json(response)


def extract_hidden(html: str, name: str) -> str:
    patterns = [
        # Microsoft currently serializes the page bootstrap object as JSON
        # embedded in a script tag. Values may contain JSON escapes such as
        # ``\\u002f`` and numeric fields are not quoted.
        rf'"{re.escape(name)}"\s*:\s*"((?:\\.|[^"\\])*)"',
        rf'"{re.escape(name)}"\s*:\s*([^,}}\s]+)',
        rf"{re.escape(name)}='([^']*)'",
        rf"{re.escape(name)}=\"([^\"]*)\"",
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            value = match.group(1)
            try:
                # Decode the same JSON escaping used by the browser page.
                return json.loads(f'"{value}"')
            except (json.JSONDecodeError, UnicodeDecodeError):
                return value
    return ""
