import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from utils import extract_verification_link, generate_password, save_api_key


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeCorouterSession:
    def __init__(self):
        self.headers = {}
        self.calls = []
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if url.endswith("/mail/groups"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": [{"id": "group-1", "name": "业务注册邮箱"}],
                }
            )
        if url.endswith("/mail/accounts"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {"id": "acct-1", "email": "box@example.com", "status": "active"}
                        ],
                        "pagination": {"pages": 1},
                    },
                    "message": "",
                }
            )
        if url.endswith("/messages"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "id": "message-1",
                                "id_mode": "graph",
                                "folder": "junkemail",
                                "subject": "Verify your email",
                                "body_preview": "Click the link below",
                            }
                        ],
                        "channel": "graph",
                    },
                }
            )
        if "/messages/message-1" in url:
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "id": "message-1",
                        "folder": "inbox",
                        "body_type": "html",
                        "body": (
                            '<a href="https://auth.tavily.com/u/email-verification?'
                            'ticket=corouter-test">Verify</a>'
                        ),
                    },
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    def close(self):
        self.closed = True


class VerificationLinkTests(unittest.TestCase):
    def test_extract_verification_link_from_html(self):
        content = (
            '<div>Click <a href="https://auth.tavily.com/u/email-verification?'
            'ticket=abc123xyz">here</a> to verify</div>'
        )
        link = extract_verification_link(content)
        self.assertEqual(
            link,
            "https://auth.tavily.com/u/email-verification?ticket=abc123xyz",
        )

    def test_extract_verification_link_returns_none_when_missing(self):
        self.assertIsNone(extract_verification_link("No link here"))


class CorouterMailProviderTests(unittest.TestCase):
    def test_acquire_email_and_wait_for_link(self):
        from corouter_mail_provider import CorouterMailProvider

        session = FakeCorouterSession()
        with patch.multiple(
            "corouter_mail_provider",
            COROUTER_MAIL_API_KEY="test-api-key",
            COROUTER_MAIL_TENANT_ID="tenant-1",
            MAX_EMAIL_WAIT_TIME=0,
            COROUTER_MAIL_REQUEST_RETRIES=1,
        ):
            provider = CorouterMailProvider(session=session)
            self.assertEqual(provider.acquire_email(), "box@example.com")
            self.assertEqual(
                provider.wait_for_verification_link(),
                "https://auth.tavily.com/u/email-verification?ticket=corouter-test",
            )
            provider.close()

        self.assertEqual(session.headers["Authorization"], "Bearer test-api-key")
        self.assertTrue(session.closed)
        self.assertTrue(provider.completed)
        detail_calls = [call for call in session.calls if "/messages/message-1" in call[0]]
        self.assertEqual(detail_calls[0][1]["folder"], "junkemail")

    def test_authorization_header_accepts_copied_bearer_value(self):
        from corouter_mail_provider import CorouterMailProvider

        session = FakeCorouterSession()
        with patch.multiple(
            "corouter_mail_provider",
            COROUTER_MAIL_API_KEY="Authorization: Bearer copied-key",
            COROUTER_MAIL_TENANT_ID="tenant-1",
        ):
            CorouterMailProvider(session=session)
        self.assertEqual(session.headers["Authorization"], "Bearer copied-key")

    def test_group_name_is_resolved_and_sent_to_account_listing(self):
        from corouter_mail_provider import CorouterMailProvider

        session = FakeCorouterSession()
        with patch.multiple(
            "corouter_mail_provider",
            COROUTER_MAIL_API_KEY="test-api-key",
            COROUTER_MAIL_TENANT_ID="tenant-1",
            MAX_EMAIL_WAIT_TIME=0,
            COROUTER_MAIL_REQUEST_RETRIES=1,
        ):
            provider = CorouterMailProvider(session=session, group_id="业务注册邮箱")
            self.assertEqual(provider.acquire_email(), "box@example.com")

        account_calls = [call for call in session.calls if call[0].endswith("/mail/accounts")]
        self.assertEqual(account_calls[0][1]["group_id"], "group-1")


class ApiKeyOutputTests(unittest.TestCase):
    def test_save_api_key_appends_clean_token(self):
        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "api_keys.txt"
            with patch("utils.API_KEYS_FILE", str(output_file)):
                key1 = save_api_key("tvly-dev-testtoken123")
                key2 = save_api_key("tvly-dev-testtoken123")
                key3 = save_api_key("tvly-dev-testtoken456")

            self.assertEqual(key1, "tvly-dev-testtoken123")
            self.assertEqual(key2, "tvly-dev-testtoken123")

            lines = output_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                lines,
                [
                    "tvly-dev-testtoken123",
                    "tvly-dev-testtoken456",
                ],
            )


class PasswordGenerationTests(unittest.TestCase):
    def test_generate_password_structure(self):
        pwd = generate_password(16)
        self.assertEqual(len(pwd), 16)
        self.assertTrue(any(c.islower() for c in pwd))
        self.assertTrue(any(c.isupper() for c in pwd))
        self.assertTrue(any(c.isdigit() for c in pwd))


class ProxyManagerTests(unittest.TestCase):
    @patch("proxy_manager.requests.get")
    def test_proxy_manager_rotation(self, mock_get):
        from proxy_manager import ProxyManager

        class Response:
            status_code = 200

            def __init__(self, *, text="", ip=""):
                self.text = text
                self.ip = ip

            def json(self):
                return {"ip": self.ip}

            def raise_for_status(self):
                return None

        proxy_1 = Response(text="192.168.1.100:8080")
        proxy_2 = Response(text="192.168.1.101:8080")
        ip_1 = Response(ip="198.51.100.1")
        ip_2 = Response(ip="198.51.100.2")
        mock_get.side_effect = [proxy_1, ip_1, proxy_2, ip_2]

        pm = ProxyManager(proxy_api_url="http://fake-api", max_attempts_per_ip=2, poll_interval=0)

        p1 = pm.get_proxy()
        self.assertEqual(p1, "http://192.168.1.100:8080")
        pm.record_attempt()
        self.assertEqual(pm.get_proxy(), "http://192.168.1.100:8080")
        pm.record_attempt()

        p2 = pm.get_proxy()
        self.assertEqual(p2, "http://192.168.1.101:8080")
        self.assertEqual(pm.attempts_on_current_ip, 0)


if __name__ == "__main__":
    unittest.main()
