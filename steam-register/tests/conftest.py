from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests

JOIN_HTML = """<input value='13703141833975910562' name='init_id' type='hidden'>
<input id="lt" value="0"><script>var g_embeddedAppID = 0; var g_bGuest = false;</script>"""
CREATION_ID = "13703141833975910001"
LINK = (
    "https://store.steampowered.com/account/newaccountverification?stoken=deadbeef&creationid="
    + CREATION_ID
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, value):
        self.now += value


@dataclass
class Response:
    data: Any = None
    text: str = ""
    status_code: int = 200
    headers: dict = field(default_factory=dict)

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False
        self.trust_env = True

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert self.responses, f"Unexpected request: {method} {urlsplit(url).path}"
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        self.closed = True


class Mailbox:
    def __init__(self, link=LINK):
        self.link = link
        self.creation_ids = []

    def wait_for_link(self, creation_id, timeout):
        self.creation_ids.append(creation_id)
        return self.link

    def close(self):
        pass


class Solver:
    def __init__(self):
        self.challenges = []

    def solve(self, challenge):
        self.challenges.append(challenge)
        return "token-" + challenge.gid

    def close(self):
        pass


def registration_responses():
    return [
        Response(text=JOIN_HTML),
        Response({"gid": "100", "type": 3, "sitekey": "live-key"}),
        Response({"success": 1, "sessionid": CREATION_ID}),
        Response(text="confirmed"),
        Response({"success": 1}),
        Response({"bAvailable": True}),
        Response({"bAvailable": True}),
        Response({"bSuccess": True, "steamid": "76561190000000000"}),
    ]
