from __future__ import annotations

from pathlib import Path
from typing import Any

from models import RegistrationResult


def _walk_dict(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dict(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dict(child)


def extract_tokens(value: Any) -> tuple[str, str]:
    """Read token names used by the original rc/ou_sq helpers."""
    refresh = ""
    access = ""
    for item in _walk_dict(value):
        if not refresh:
            for key in ("refresh_token", "refreshToken", "reftoken"):
                if item.get(key):
                    refresh = str(item[key])
                    break
        if not access:
            for key in ("access_token", "accessToken"):
                if item.get(key):
                    access = str(item[key])
                    break
        if refresh and access:
            break
    return refresh, access


def append_success_txt(path: Path, result: RegistrationResult) -> bool:
    """Append account----password----client_id----refresh_token for completed accounts."""
    if not result.success or result.dry_run:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # The original TaskWorker uses `----` as its account field separator.
    line = f"{result.member_name}----{result.password}----{result.client_id}----{result.refresh_token}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return True
