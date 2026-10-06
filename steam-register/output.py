from __future__ import annotations

import csv
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from models import RegistrationInput, RegistrationResult

FIELDS = ("account_name", "password", "email", "steamid", "saved_at")


@contextmanager
def _append(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        stream: TextIO = os.fdopen(fd, "a", encoding="utf-8", newline="")
    except BaseException:
        os.close(fd)
        raise
    with stream:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


def registered_emails(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"账号明细表头与当前格式不一致：{path}")
        return {row["email"].casefold() for row in reader if row.get("email")}


def save_account(result: RegistrationResult, output: Path, text_output: Path | None = None) -> None:
    if not result.success:
        return
    registered_emails(output)  # 追加前检查已有表头，避免写入不兼容的历史文件。
    with _append(output) as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        if stream.tell() == 0:
            writer.writeheader()
        writer.writerow(
            {
                "account_name": result.account_name,
                "password": result.password,
                "email": result.email,
                "steamid": result.steamid,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    if text_output:
        with _append(text_output) as stream:
            stream.write(f"{result.account_name}----{result.password}----{result.email}\n")


def save_failure(result: RegistrationResult, path: Path) -> None:
    if result.success:
        return
    with _append(path) as stream:
        stream.write(json.dumps(result.as_dict(), ensure_ascii=False) + "\n")


def save_attempt(fields: RegistrationInput, path: Path) -> None:
    with _append(path) as stream:
        stream.write(
            json.dumps(
                {
                    "account_name": fields.account_name,
                    "email": fields.email,
                    "password": fields.password,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
