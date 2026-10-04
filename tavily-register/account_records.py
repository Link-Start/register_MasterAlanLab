"""Persist complete Tavily credentials alongside the legacy key-only export."""

import csv
import hashlib
import io
import os
from datetime import datetime
from pathlib import Path


ACCOUNTS_FILE = "accounts.csv"
ACCOUNT_FIELDS = ("api_key", "email", "password", "saved_at", "key_sha256", "source")


class ResultPersistenceError(Exception):
    """Stop the batch if a retrieved key could not be durably saved."""


def resolve_accounts_file(output_file: str, accounts_file: str | None = None) -> str:
    """Default to an account journal next to the key export, not the process cwd."""
    path = accounts_file or str(Path(output_file).with_name(ACCOUNTS_FILE))
    same_path = os.path.realpath(path) == os.path.realpath(output_file)
    if not same_path and os.path.exists(path) and os.path.exists(output_file):
        same_path = os.path.samefile(path, output_file)
    if same_path:
        raise ValueError("账号明细和纯 Key 输出必须使用不同文件")
    return str(path)


def _write_private_text(
    file_path: str, text: str, mode: str = "a", *, header: str = ""
) -> None:
    """Create owner-only files and flush each result before returning."""
    flags = os.O_WRONLY | os.O_CREAT
    if mode == "a":
        flags |= os.O_APPEND
    elif mode == "w":
        flags |= os.O_TRUNC
    elif mode == "x":
        flags |= os.O_EXCL
    else:
        raise ValueError(f"不支持的输出模式: {mode}")
    fd = os.open(file_path, flags, 0o600)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as file_obj:
            fd = None  # fdopen now owns the descriptor, including on write errors.
            if header and os.fstat(file_obj.fileno()).st_size == 0:
                file_obj.write(header)
            file_obj.write(text)
            file_obj.flush()
            os.fsync(file_obj.fileno())
    finally:
        if fd is not None:
            os.close(fd)


def save_result(
    file_path: str,
    email: str,
    api_key: str,
    mode: str = "a",
    *,
    password: str,
    accounts_file: str | None = None,
    source: str = "signup",
) -> str:
    """Save the credential journal first, then the compatible key-only export.

    The journal always appends, even when the key export uses ``mode='w'``.
    Return a SHA-256 identifier that can be written to ordinary logs without
    exposing the key or password.
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("成功记录需要非空 API Key")
    if not email or "@" not in email or not password:
        raise ValueError("成功记录需要邮箱和 Tavily 账号密码")
    if "\n" in key or "\r" in key:
        raise ValueError("API Key 必须是单行字符串")
    if mode not in {"a", "w", "x"}:
        raise ValueError(f"不支持的输出模式: {mode}")

    accounts_path = resolve_accounts_file(file_path, accounts_file)
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    record = {
        "email": email.strip(),
        "password": password,
        "api_key": key,
        "key_sha256": fingerprint,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source,
    }
    # CSV quoting preserves commas, quotes and newlines in actual passwords.
    header_buffer = io.StringIO(newline="")
    csv.DictWriter(
        header_buffer, fieldnames=ACCOUNT_FIELDS, lineterminator="\n"
    ).writeheader()
    row_buffer = io.StringIO(newline="")
    csv.DictWriter(
        row_buffer, fieldnames=ACCOUNT_FIELDS, lineterminator="\n"
    ).writerow(record)
    try:
        _write_private_text(
            accounts_path, row_buffer.getvalue(), header=header_buffer.getvalue()
        )
    except OSError as exc:
        raise ResultPersistenceError(
            f"账号明细写入失败，批次已停止: {accounts_path}"
        ) from exc
    try:
        _write_private_text(file_path, f"{key}\n", mode)
    except OSError as exc:
        raise ResultPersistenceError(
            f"纯 Key 输出写入失败，完整账号已保存到 {accounts_path}；批次已停止"
        ) from exc
    return fingerprint
