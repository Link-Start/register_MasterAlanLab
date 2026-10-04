import csv
import hashlib
import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import account_records
import main
from account_records import ResultPersistenceError, resolve_accounts_file, save_result


KEY = "tvly-dev-fixture-only-0123456789abcdef"
PASSWORD = '  Mixed!密码"\\,----\nAa12  '


def read_records(path):
    with Path(path).open(encoding="utf-8", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


class AccountRecordTests(unittest.TestCase):
    def test_save_complete_record_and_legacy_export(self):
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "keys.txt"
            fingerprint = save_result(
                str(output), "user@example.com", f" {KEY} ",
                password=PASSWORD, source="email_verification",
            )
            record, = read_records(Path(tmpdir) / "accounts.csv")
            self.assertEqual(record["email"], "user@example.com")
            self.assertEqual(record["password"], PASSWORD)
            self.assertEqual(record["api_key"], KEY)
            self.assertEqual(record["source"], "email_verification")
            self.assertEqual(fingerprint, hashlib.sha256(KEY.encode()).hexdigest())
            self.assertEqual(record["key_sha256"], fingerprint)
            self.assertIsNotNone(datetime.fromisoformat(record["saved_at"]).tzinfo)
            self.assertEqual(output.read_text(), f"{KEY}\n")
            # A quoted password can contain a newline without adding a record.
            self.assertEqual(len(read_records(Path(tmpdir) / "accounts.csv")), 1)

    def test_custom_journal_and_repeated_runs_append(self):
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "keys.txt"
            journal = Path(tmpdir) / "custom-accounts.csv"
            for index in range(2):
                save_result(
                    str(output), f"user{index}@example.com", f"{KEY}-{index}",
                    password=f"password-{index}", accounts_file=str(journal),
                )
            self.assertEqual(len(read_records(journal)), 2)
            self.assertEqual(output.read_text().splitlines(), [f"{KEY}-0", f"{KEY}-1"])
            self.assertFalse((Path(tmpdir) / "accounts.csv").exists())
            self.assertEqual(journal.read_text().count("api_key,email,password,"), 1)

    def test_empty_existing_csv_gets_header_and_key_overwrite_keeps_history(self):
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "keys.txt"
            journal = Path(tmpdir) / "accounts.csv"
            journal.touch()
            save_result(str(output), "first@example.com", KEY, password=PASSWORD)
            save_result(
                str(output), "second@example.com", f"{KEY}-next",
                password=PASSWORD, mode="w",
            )
            self.assertEqual(
                [row["email"] for row in read_records(journal)],
                ["first@example.com", "second@example.com"],
            )
            self.assertEqual(output.read_text(), f"{KEY}-next\n")
            self.assertEqual(journal.read_text().count("api_key,email,password,"), 1)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_new_and_existing_outputs_are_owner_only(self):
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "keys.txt"
            journal = Path(tmpdir) / "accounts.csv"
            save_result(str(output), "user@example.com", KEY, password=PASSWORD)
            for path in (output, journal):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                path.chmod(0o644)
            save_result(str(output), "next@example.com", KEY, password=PASSWORD)
            for path in (output, journal):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_journal_is_flushed_before_key_export(self):
        with TemporaryDirectory() as tmpdir:
            output = str(Path(tmpdir) / "keys.txt")
            journal = str(Path(tmpdir) / "accounts.csv")
            with patch("account_records.os.fsync", wraps=os.fsync) as fsync:
                with patch(
                    "account_records._write_private_text",
                    wraps=account_records._write_private_text,
                ) as write:
                    save_result(output, "user@example.com", KEY, password=PASSWORD)
            self.assertEqual([call.args[0] for call in write.call_args_list], [journal, output])
            self.assertEqual(fsync.call_count, 2)

    def test_journal_error_does_not_export_unmapped_key(self):
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "keys.txt"
            with self.assertRaises(ResultPersistenceError):
                save_result(
                    str(output), "user@example.com", KEY, password=PASSWORD,
                    accounts_file=tmpdir,  # a directory is not writable as a file
                )
            self.assertFalse(output.exists())

    def test_key_export_error_keeps_complete_account(self):
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "missing-parent" / "keys.txt"
            journal = Path(tmpdir) / "accounts.csv"
            with self.assertRaises(ResultPersistenceError):
                save_result(
                    str(output), "user@example.com", KEY,
                    password=PASSWORD, accounts_file=str(journal),
                )
            self.assertEqual(read_records(journal)[0]["api_key"], KEY)

    def test_reject_same_path_and_filesystem_aliases(self):
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "keys.txt"
            output.write_text("original\n")
            alias = Path(tmpdir) / "alias.csv"
            alias.symlink_to(output)
            hardlink = Path(tmpdir) / "hardlink.csv"
            os.link(output, hardlink)
            for path in (output, alias, hardlink):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    resolve_accounts_file(str(output), str(path))
            self.assertEqual(output.read_text(), "original\n")

    def test_incomplete_or_multiline_credentials_are_not_written(self):
        with TemporaryDirectory() as tmpdir:
            output = str(Path(tmpdir) / "keys.txt")
            cases = [
                ("user@example.com", "", PASSWORD),
                ("user@example.com", KEY, ""),
                ("invalid-address", KEY, PASSWORD),
                ("user@example.com", f"{KEY}\nother-key", PASSWORD),
            ]
            for email, key, password in cases:
                with self.subTest(email=email, key=key), self.assertRaises(ValueError):
                    save_result(output, email, key, password=password)
            self.assertFalse(Path(output).exists())
            self.assertFalse((Path(tmpdir) / "accounts.csv").exists())


class BatchAccountRecordTests(unittest.TestCase):
    def run_batch(self, tmpdir, signup_result, *, password=None, accounts_file=None, count=1):
        provider = Mock()
        manager = Mock(proxy_api_url="")
        manager.get_proxy.return_value = None
        console = io.StringIO()
        with patch("main.load_config", return_value={}), patch(
            "main.ProxyManager", return_value=manager
        ), patch("main.create_mail_provider", return_value=provider), patch(
            "main.signup", return_value=signup_result
        ) as signup_call, patch(
            "main._verify_email_and_get_key", return_value=KEY
        ) as verify_call, patch(
            "main.try_login_get_key", return_value=KEY
        ) as login_call, patch(
            "main.generate_password", return_value=PASSWORD
        ) as generate_password, patch("main.time.sleep"), redirect_stdout(console):
            main.batch_signup(
                emails=[f"user{index}@example.com" for index in range(count)],
                output_file=str(Path(tmpdir) / "keys.txt"),
                accounts_file=accounts_file,
                failed_file=str(Path(tmpdir) / "failed.txt"),
                registered_emails_file=str(Path(tmpdir) / "registered.txt"),
                run_log_file=str(Path(tmpdir) / "run.log"),
                password=password,
                interval=0,
            )
        return provider, signup_call, verify_call, login_call, generate_password, console.getvalue()

    def test_all_key_acquisition_paths_save_actual_password(self):
        cases = [
            ({"success": True, "api_keys": [{"key": KEY}]}, "signup"),
            ({"success": True, "api_keys": None}, "email_verification"),
            ({"success": False, "error": "temporary_error"}, "login_recovery"),
        ]
        for result, source in cases:
            for fixed_password in (None, "Fixed!Password123"):
                with self.subTest(source=source, fixed=fixed_password is not None), TemporaryDirectory() as tmpdir:
                    values = self.run_batch(tmpdir, result, password=fixed_password)
                    provider, signup_call, verify_call, login_call, generate, console = values
                    record, = read_records(Path(tmpdir) / "accounts.csv")
                    actual_password = fixed_password or PASSWORD
                    self.assertEqual(record["api_key"], KEY)
                    self.assertEqual(record["email"], "user0@example.com")
                    self.assertEqual(record["password"], actual_password)
                    self.assertEqual(record["source"], source)
                    self.assertEqual(signup_call.call_args.kwargs["password"], actual_password)
                    if source == "email_verification":
                        self.assertEqual(verify_call.call_args.args[2], actual_password)
                    if source == "login_recovery":
                        self.assertEqual(login_call.call_args.args[1], actual_password)
                    self.assertEqual(generate.call_count, int(fixed_password is None))
                    provider.close.assert_called_once()
                    log = (Path(tmpdir) / "run.log").read_text()
                    self.assertIn(f"key_sha256={record['key_sha256']}", log)
                    self.assertNotIn(actual_password, console)
                    self.assertNotIn(actual_password, log)
                    self.assertNotIn(KEY, log)

    def test_batch_stops_and_closes_resources_on_persistence_error(self):
        with TemporaryDirectory() as tmpdir:
            provider = Mock()
            session = Mock()
            with patch("main.create_mail_provider", return_value=provider), patch(
                "main.signup", return_value={"success": True, "api_keys": [KEY], "session": session}
            ) as signup_call, patch("main.load_config", return_value={}), patch(
                "main.ProxyManager", return_value=Mock(proxy_api_url="")
            ), redirect_stdout(io.StringIO()), self.assertRaises(ResultPersistenceError):
                main.batch_signup(
                    emails=["first@example.com", "second@example.com"],
                    output_file=str(Path(tmpdir) / "keys.txt"),
                    accounts_file=tmpdir,
                    failed_file=str(Path(tmpdir) / "failed.txt"),
                    registered_emails_file=str(Path(tmpdir) / "registered.txt"),
                    run_log_file=str(Path(tmpdir) / "run.log"),
                    password="Fixed!Password123",
                )
            self.assertEqual(signup_call.call_count, 1)
            provider.close.assert_called_once()
            session.close.assert_called_once()
            self.assertFalse((Path(tmpdir) / "keys.txt").exists())

    def test_custom_account_path_is_used_by_batch(self):
        with TemporaryDirectory() as tmpdir:
            journal = Path(tmpdir) / "custom.csv"
            self.run_batch(
                tmpdir, {"success": True, "api_keys": [KEY]},
                accounts_file=str(journal),
            )
            self.assertEqual(read_records(journal)[0]["api_key"], KEY)
            self.assertFalse((Path(tmpdir) / "accounts.csv").exists())

    def test_retry_forwards_custom_account_path(self):
        with TemporaryDirectory() as tmpdir:
            failed = Path(tmpdir) / "failed.txt"
            failed.write_text("user@example.com----temporary_error\n")
            journal = str(Path(tmpdir) / "custom.csv")
            with patch("main.batch_signup") as batch, redirect_stdout(io.StringIO()):
                main.retry_failed(
                    failed_file=str(failed),
                    output_file=str(Path(tmpdir) / "keys.txt"),
                    accounts_file=journal,
                    password="Fixed!Password123",
                )
            self.assertEqual(batch.call_args.kwargs["accounts_file"], journal)
            self.assertEqual(batch.call_args.kwargs["password"], "Fixed!Password123")

    def test_retry_checks_conflicting_paths_before_clearing_failed_file(self):
        with TemporaryDirectory() as tmpdir:
            failed = Path(tmpdir) / "failed.txt"
            original = "user@example.com----temporary_error\n"
            failed.write_text(original)
            with patch("main.batch_signup") as batch, self.assertRaises(ValueError):
                main.retry_failed(
                    failed_file=str(failed),
                    output_file=str(Path(tmpdir) / "keys.txt"),
                    accounts_file=str(failed),
                )
            self.assertEqual(failed.read_text(), original)
            batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
