import csv
import json
import os
import stat

import pytest
from conftest import LINK, Mailbox, Response, Session, Solver, registration_responses

import corouter_mail_provider as corouter
import main
from config import load_config
from http_client import SteamSession
from models import MailboxRecord, RegistrationResult
from output import registered_emails, save_account, save_failure


def test_success_csv_preserves_credentials_and_failure_is_redacted(tmp_path):
    path = tmp_path / "accounts.csv"
    result = RegistrationResult(True, "user@example.com", "steam_user", 'Password,"123!')
    save_account(result, path)
    with path.open(newline="") as stream:
        records = list(csv.DictReader(stream))
    assert records[0]["password"] == result.password
    assert registered_emails(path) == {"user@example.com"}
    assert "password" not in result.as_dict()
    result.success = False
    result.code = "OUTCOME_UNKNOWN"
    save_account(result, path)
    assert len(path.read_text().splitlines()) == 2
    failure_path = tmp_path / "failed.jsonl"
    save_failure(result, failure_path)
    assert result.password not in failure_path.read_text()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_incompatible_csv_is_preserved(tmp_path):
    path = tmp_path / "accounts.csv"
    path.write_text("email,password\nuser@example.com,old\n")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        save_account(RegistrationResult(True, "other@example.com", "other", "Abc123!xyz"), path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_wait_settings_are_rejected(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(f"STEAM_POLL_INTERVAL: {value}\n")
    with pytest.raises(ValueError):
        load_config(path)


def test_cli_unknown_creation_keeps_generated_password_without_retry(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("STEAM_TEXT_OUTPUT: ''\n")
    responses = registration_responses()
    responses[-1].data = {"changed": True}
    transport = Session(responses)
    monkeypatch.setattr(main, "SteamSession", lambda settings: SteamSession(settings, transport))
    monkeypatch.setattr(main, "create_mail_provider", lambda *args, **kwargs: Mailbox(LINK))
    monkeypatch.setattr(main, "ManualCaptchaSolver", lambda *args: Solver())
    assert main.main(["--config", str(config_path), "--email", "user@example.com", "--json"]) == 1
    attempt = json.loads((tmp_path / "attempts.jsonl").read_text())
    create_password = transport.calls[-1][2]["data"]["password"]
    assert attempt["password"] == create_password and len(create_password) >= 8
    assert not (tmp_path / "accounts.csv").exists()
    assert create_password not in capsys.readouterr().out
    assert transport.closed


def test_cli_skips_successful_mailbox_without_network(tmp_path, monkeypatch):
    save_account(
        RegistrationResult(True, "user@example.com", "steam_user", "Abc123!xyz"),
        tmp_path / "accounts.csv",
    )
    path = tmp_path / "config.yaml"
    path.write_text("{}")

    def unexpected(*args, **kwargs):
        raise AssertionError("Network must not be used")

    monkeypatch.setattr(main, "SteamSession", unexpected)
    assert main.main(["--config", str(path), "--email", "USER@example.com"]) == 0


def test_invalid_graph_configuration_fails_before_network(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("MAIL_PROVIDER: graph\n")

    def unexpected(*args, **kwargs):
        raise AssertionError("Network must not be used")

    monkeypatch.setattr(main, "SteamSession", unexpected)
    assert main.main(["--config", str(path), "--email", "user@example.com"]) == 2


def test_output_paths_cannot_overlap(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("STEAM_ATTEMPTS_OUTPUT: accounts.csv\n")
    with pytest.raises(ValueError, match="不同文件"):
        load_config(path)


def test_cli_corouter_selects_unused_accounts_and_respects_count(tmp_path, monkeypatch, capsys):
    from conftest import Response

    path = tmp_path / "config.yaml"
    path.write_text(
        "MAIL_PROVIDER: corouter\n"
        "COROUTER_MAIL_API_KEY: test-secret\n"
        "COROUTER_MAIL_TENANT_ID: tenant-one\n"
        "BATCH_DELAY: 0\n"
    )
    save_account(
        RegistrationResult(True, "used@example.com", "existing", "Abc123!xyz"),
        tmp_path / "accounts.csv",
    )
    api = Session(
        [
            Response(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {"id": "used", "email": "USED@example.com", "status": "active"},
                            {"id": "a", "email": "first@example.com", "status": "active"},
                            {"id": "b", "email": "second@example.com", "status": "active"},
                            {"id": "c", "email": "third@example.com", "status": "active"},
                        ],
                        "pagination": {"pages": 1},
                    },
                }
            )
        ]
    )
    transports = []
    selected = []

    def steam_client(settings):
        transport = Session(registration_responses())
        transports.append(transport)
        return SteamSession(settings, transport)

    def mailbox(record, *args, **kwargs):
        selected.append((record.email, record.account_id))
        return Mailbox(LINK)

    monkeypatch.setattr(corouter.requests, "Session", lambda: api)
    monkeypatch.setattr(main, "SteamSession", steam_client)
    monkeypatch.setattr(main, "create_mail_provider", mailbox)
    monkeypatch.setattr(main, "ManualCaptchaSolver", lambda *args: Solver())
    assert main.main(["--config", str(path), "--count", "2", "--json"]) == 0
    assert selected == [("first@example.com", "a"), ("second@example.com", "b")]
    assert api.closed and all(transport.closed for transport in transports)
    assert registered_emails(tmp_path / "accounts.csv") == {
        "used@example.com",
        "first@example.com",
        "second@example.com",
    }
    assert "test-secret" not in capsys.readouterr().out


def test_cli_corouter_missing_credentials_fails_before_steam(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("MAIL_PROVIDER: corouter\n")

    def unexpected(*args, **kwargs):
        raise AssertionError("Steam must not be contacted")

    monkeypatch.setattr(main, "SteamSession", unexpected)
    assert main.main(["--config", str(path)]) == 2
    assert "COROUTER_MAIL_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "attempts.jsonl").exists()


def test_cli_corouter_reads_verification_mail_and_saves_account(tmp_path, monkeypatch, capsys):
    from conftest import Response

    path = tmp_path / "config.yaml"
    path.write_text(
        "MAIL_PROVIDER: corouter\n"
        "COROUTER_MAIL_API_KEY: test-secret\n"
        "COROUTER_MAIL_TENANT_ID: tenant-one\n"
    )
    discovery = Session(
        [
            Response({"code": 0, "data": [{"id": "steam-group", "name": "Steam"}]}),
            Response(
                {
                    "code": 0,
                    "data": {
                        "items": [{"id": "one", "email": "user@example.com", "status": "active"}],
                        "pagination": {"pages": 1},
                    },
                }
            ),
        ]
    )
    mail_api = Session(
        [
            Response(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "id": "message-one",
                                "folder": "junk",
                                "id_mode": "rest",
                                "subject": "Steam email verification",
                            }
                        ]
                    },
                }
            ),
            Response({"code": 0, "data": {"body": LINK, "body_type": "text"}}),
        ]
    )
    api_sessions = iter([discovery, mail_api])
    transport = Session(registration_responses())
    monkeypatch.setattr(corouter.requests, "Session", lambda: next(api_sessions))
    monkeypatch.setattr(main, "SteamSession", lambda config: SteamSession(config, transport))
    monkeypatch.setattr(main, "ManualCaptchaSolver", lambda *args: Solver())
    assert main.main(["--config", str(path), "--mail-group", "steam", "--json"]) == 0
    assert discovery.calls[1][2]["params"]["group_id"] == "steam-group"
    assert mail_api.calls[1][2]["params"] == {"folder": "junk", "id_mode": "rest"}
    assert "/mail/accounts/one/messages" in mail_api.calls[0][1]
    assert registered_emails(tmp_path / "accounts.csv") == {"user@example.com"}
    assert discovery.closed and mail_api.closed and transport.closed
    assert "test-secret" not in capsys.readouterr().out


def test_cli_corouter_quota_exhaustion_stops_the_batch(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("MAIL_PROVIDER: corouter\nBATCH_DELAY: 0\n")
    records = [
        MailboxRecord(f"user{number}@example.com", account_id=str(number)) for number in range(3)
    ]
    monkeypatch.setattr(main, "load_corouter_mailboxes", lambda *args: records)
    monkeypatch.setattr(main, "create_mail_provider", lambda *args, **kwargs: Mailbox(LINK))
    monkeypatch.setattr(main, "ManualCaptchaSolver", lambda *args: Solver())
    monkeypatch.setattr(main, "SteamSession", lambda config: SteamSession(config, Session([])))
    calls = []

    class Registrar:
        def __init__(self, *args, **kwargs):
            pass

        def register(self, fields):
            calls.append(fields.email)
            return RegistrationResult(
                False,
                fields.email,
                fields.account_name,
                fields.password,
                stage="mail",
                code="MAIL_QUOTA_EXCEEDED",
                message="取件额度已用尽",
            )

    monkeypatch.setattr(main, "SteamRegistrar", Registrar)
    assert main.main(["--config", str(path), "--all"]) == 1
    assert calls == ["user0@example.com"]
    assert len((tmp_path / "attempts.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("creation_response", [{"bSuccess": True}, {"unexpected": True}])
def test_renamed_account_is_saved_in_success_or_recovery_files(
    tmp_path, monkeypatch, capsys, creation_response
):
    path = tmp_path / "config.yaml"
    path.write_text("STEAM_TEXT_OUTPUT: accounts.txt\n")
    names = iter(["QingFeng4827", "XingHe7316"])
    responses = registration_responses()
    responses.insert(5, Response({"bAvailable": False}))
    responses[-1] = Response(creation_response)
    transport = Session(responses)
    solver = Solver()
    mailbox = Mailbox(LINK)
    monkeypatch.setattr(main, "random_account_name", lambda prefix: next(names))
    monkeypatch.setattr(main, "SteamSession", lambda config: SteamSession(config, transport))
    monkeypatch.setattr(main, "create_mail_provider", lambda *args, **kwargs: mailbox)
    monkeypatch.setattr(main, "ManualCaptchaSolver", lambda *args: solver)

    success = creation_response.get("bSuccess") is True
    assert main.main(["--config", str(path), "--email", "user@example.com", "--json"]) == (
        0 if success else 1
    )
    attempts = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert [attempt["account_name"] for attempt in attempts] == ["QingFeng4827", "XingHe7316"]
    create = transport.calls[-1][2]["data"]
    assert create["accountname"] == attempts[-1]["account_name"] == "XingHe7316"
    assert create["password"] == attempts[-1]["password"] == attempts[0]["password"]
    terminal = json.loads(capsys.readouterr().out)
    assert terminal["account_name"] == "XingHe7316" and create["password"] not in repr(terminal)
    if success:
        with (tmp_path / "accounts.csv").open(newline="") as stream:
            saved = next(csv.DictReader(stream))
        assert saved["account_name"] == "XingHe7316" and saved["password"] == create["password"]
        assert (tmp_path / "accounts.txt").read_text() == (
            f"XingHe7316----{create['password']}----user@example.com\n"
        )
    else:
        assert terminal["code"] == "OUTCOME_UNKNOWN"
        assert not (tmp_path / "accounts.csv").exists()
        assert not (tmp_path / "accounts.txt").exists()
        assert json.loads((tmp_path / "failed.jsonl").read_text())["account_name"] == "XingHe7316"
    assert len(solver.challenges) == 1 and len(mailbox.creation_ids) == 1
    assert len([call for call in transport.calls if call[1].endswith("/createaccount/")]) == 1


def test_user_specified_name_is_not_automatically_replaced(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    responses = registration_responses()[:6]
    responses[-1] = Response({"bAvailable": False})
    transport = Session(responses)
    monkeypatch.setattr(main, "SteamSession", lambda config: SteamSession(config, transport))
    monkeypatch.setattr(main, "create_mail_provider", lambda *args, **kwargs: Mailbox(LINK))
    monkeypatch.setattr(main, "ManualCaptchaSolver", lambda *args: Solver())

    def unexpected(*args):
        raise AssertionError("Explicit account names must be kept")

    monkeypatch.setattr(main, "random_account_name", unexpected)
    assert (
        main.main(
            [
                "--config",
                str(path),
                "--email",
                "user@example.com",
                "--account-name",
                "QingFeng4827",
                "--json",
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "NAME_UNAVAILABLE" and result["account_name"] == "QingFeng4827"
    assert len(transport.calls) == 6


def test_failure_to_save_renamed_credentials_stops_before_account_creation(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("{}")
    responses = registration_responses()[:6]
    responses[-1] = Response({"bAvailable": False})
    transport = Session(responses)
    names = iter(["QingFeng4827", "XingHe7316"])
    monkeypatch.setattr(main, "random_account_name", lambda prefix: next(names))
    monkeypatch.setattr(main, "SteamSession", lambda config: SteamSession(config, transport))
    monkeypatch.setattr(main, "create_mail_provider", lambda *args, **kwargs: Mailbox(LINK))
    monkeypatch.setattr(main, "ManualCaptchaSolver", lambda *args: Solver())
    save_attempt = main.save_attempt

    def persist(fields, output):
        if fields.account_name == "XingHe7316":
            raise OSError("disk full")
        save_attempt(fields, output)

    monkeypatch.setattr(main, "save_attempt", persist)
    assert main.main(["--config", str(path), "--email", "user@example.com"]) == 2
    assert all(not call[1].endswith("/createaccount/") for call in transport.calls)
    assert not (tmp_path / "accounts.csv").exists()
