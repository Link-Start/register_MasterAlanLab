import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from utils import extract_verification_link, generate_password, save_api_key


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeOutlookTwSession:
    def __init__(self):
        self.headers = {}
        self.calls = []
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if url.endswith("/api/generate"):
            return FakeResponse(
                {
                    "email": "testbox@outlook.tw",
                    "expires": 123456789,
                    "anonymous": True,
                }
            )
        if url.endswith("/api/emails"):
            return FakeResponse([{"id": 42, "subject": "Verify your email"}])
        if url.endswith("/api/email/42"):
            return FakeResponse(
                {
                    "id": 42,
                    "html_content": (
                        '<a href="https://auth.tavily.com/u/email-verification?'
                        'ticket=outlook-tw-test">Verify</a>'
                    ),
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


class OutlookTwProviderTests(unittest.TestCase):
    def test_acquire_email_and_wait_for_link(self):
        from outlook_tw_provider import OutlookTwProvider

        session = FakeOutlookTwSession()
        provider = OutlookTwProvider(session=session)

        email = provider.acquire_email()
        link = provider.wait_for_verification_link()
        provider.close()

        self.assertEqual(email, "testbox@outlook.tw")
        self.assertEqual(
            link,
            "https://auth.tavily.com/u/email-verification?ticket=outlook-tw-test",
        )
        self.assertTrue(provider.completed)
        self.assertTrue(session.closed)


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

        mock_get.return_value.status_code = 200
        mock_get.return_value.text = "192.168.1.100:8080"

        pm = ProxyManager(proxy_api_url="http://fake-api", max_attempts_per_ip=2, poll_interval=0)
        
        # 提取第一个 IP
        p1 = pm.get_proxy()
        self.assertEqual(p1, "http://192.168.1.100:8080")
        
        # 使用 1 次
        pm.record_attempt()
        self.assertEqual(pm.get_proxy(), "http://192.168.1.100:8080")
        
        # 使用第 2 次，达到最大限制
        pm.record_attempt()

        # 模拟下一个提取到的 IP 变化
        mock_get.return_value.text = "192.168.1.101:8080"
        p2 = pm.get_proxy()
        self.assertEqual(p2, "http://192.168.1.101:8080")
        self.assertEqual(pm.attempts_on_current_ip, 0)


if __name__ == "__main__":
    unittest.main()

