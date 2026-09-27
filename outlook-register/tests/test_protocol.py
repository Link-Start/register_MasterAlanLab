from datetime import date

from fingerprint import build_fingerprint, random_birth_date
from models import RegistrationInput
from risk import extract_hidden


def test_fingerprint_contains_recovered_fields():
    fp = build_fingerprint(uaid="a" * 32)
    assert fp.uaid == "a" * 32
    assert fp.url_dfp.startswith("bua=")
    assert "&os=Win32" in fp.url_dfp
    assert fp.webgl["webgl_vendor"] == "WebKit"


def test_fingerprint_uses_configured_timezone():
    fp = build_fingerprint(uaid="a" * 32, timezone=540, language="ja-JP")
    assert "&tz=540&" in fp.url_dfp
    assert "&tzo=540&" in fp.url_dfp
    assert "&bl=ja-JP&" in fp.url_dfp


def test_registration_input_shape():
    item = RegistrationInput(
        member_name="test@example.com",
        password="Password123!",
        birth_date=date(1990, 1, 2),
    )
    assert item.country == "US"
    assert random_birth_date().year in range(1980, 2006)


def test_extract_hidden_decodes_json_escapes_and_numbers():
    html = r'{"apiCanary":"abc\u002fdef","hpgid":200225}'
    assert extract_hidden(html, "apiCanary") == "abc/def"
    assert extract_hidden(html, "hpgid") == "200225"


def test_success_txt_contains_account_password_and_token(tmp_path):
    from models import RegistrationResult
    from output import append_success_txt

    result = RegistrationResult(
        success=True,
        member_name="a@example.com",
        password="Password123!",
        refresh_token="REFRESH",
    )
    path = tmp_path / "accounts.txt"
    assert append_success_txt(path, result)
    assert path.read_text(encoding="utf-8") == "a@example.com----Password123!----9e5f94bc-e8a4-4e73-b8be-63364c29d753----REFRESH\n"

    dry = RegistrationResult(
        success=True,
        member_name="dry@example.com",
        password="Password123!",
        dry_run=True,
    )
    assert append_success_txt(path, dry) is False
    assert path.read_text(encoding="utf-8").count("\n") == 1
