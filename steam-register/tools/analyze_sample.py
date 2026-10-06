"""静态解析本附件的 Nuitka onefile 和常量容器；不加载或执行附件中的代码。"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pefile
import zstandard

LIMIT = 512 * 1024 * 1024
FIELDS = {
    "email",
    "captchagid",
    "captcha_text",
    "elang",
    "init_id",
    "guest",
    "accountname",
    "password",
    "count",
    "lt",
    "creation_sessionid",
    "embedded_appid",
    "bSuccess",
    "gid",
    "sitekey",
    "captchaType",
    "HCaptchaSteam",
    "siteReferer",
    "siteKey",
    "taskId",
    "Success",
    "Fail",
    "response",
    "captcha_key",
    "client_id",
    "refresh_token",
    "scope",
    "grant_type",
    "getrefreshcaptcha",
    "sendmail",
    "streg",
    "getsteamreg",
    "pyimap",
    "pypop",
    "wrgraph",
}
HOSTS = {
    "store.steampowered.com",
    "api.captcha-run.com",
    "graph.microsoft.com",
    "login.microsoftonline.com",
}


def resource(pe: Any, identifier: int) -> tuple[bytes, int]:
    for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if kind.id != 10:
            continue
        for entry in kind.directory.entries:
            if entry.id == identifier:
                item = entry.directory.entries[0].data.struct
                return pe.get_data(item.OffsetToData, item.Size), pe.get_offset_from_rva(
                    item.OffsetToData
                )
    raise ValueError(f"未找到 RCDATA/{identifier}")


def payload_files(payload: bytes) -> tuple[bytes, list[dict[str, Any]], int]:
    if payload[:3] != b"KAY":
        raise ValueError("本工具只支持附件使用的 KAY/Zstandard Nuitka onefile 格式")
    decoder = zstandard.ZstdDecompressor().decompressobj()
    raw = bytearray()
    for start in range(3, len(payload), 65536):
        raw.extend(decoder.decompress(payload[start : start + 65536]))
        if len(raw) > LIMIT:
            raise ValueError("解压数据超过 512 MiB")
        if decoder.eof:
            break  # 本附件的 Zstandard 帧后还有 9 字节尾部数据。
    if not decoder.eof:
        raise ValueError("Zstandard 帧不完整")
    position = 0
    files = []
    main = b""
    while position < len(raw):
        start = position
        while raw[position : position + 2] != b"\0\0":
            position += 2
            if position - start > 4096 or position + 2 > len(raw):
                raise ValueError("非法的 UTF-16 文件名")
        name = raw[start:position].decode("utf-16le")
        position += 2
        if not name:
            break
        size = struct.unpack_from("<Q", raw, position)[0]
        position += 8
        if size > len(raw) - position:
            raise ValueError("onefile 文件大小越界")
        files.append({"name": name, "size": size})
        if name == "main.dll":
            main = bytes(raw[position : position + size])
        position += size
    if not main:
        raise ValueError("未找到 main.dll")
    return main, files, len(raw)


class Constants:
    """本附件 Python 3.10/Nuitka 常量标签；只构造数据，不创建可执行 code object。"""

    def __init__(self, data: bytes):
        self.data = data
        self.position = 2

    def take(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self.data):
            raise ValueError("常量长度越界")
        data = self.data[self.position : self.position + size]
        self.position += size
        return data

    def varint(self) -> int:
        value = 0
        for shift in range(0, 70, 7):
            byte = self.take(1)[0]
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("整数常量长度越界")

    def cstring(self) -> bytes:
        end = self.data.index(b"\0", self.position)
        value = self.data[self.position : end]
        self.position = end + 1
        return value

    def many(self, count: int) -> list[Any]:
        if count > len(self.data):
            raise ValueError("常量个数越界")
        values: list[Any] = []
        for _ in range(count):
            values.append(self.one(values))
        return values

    def one(self, previous: list[Any] | None = None) -> Any:
        tag = self.take(1).decode("ascii")
        if tag == "p":
            return previous[-1] if previous else None
        if tag in {"n", "s", "t", "F"}:
            return {"n": None, "s": "", "t": True, "F": False}[tag]
        if tag in {"a", "u", "O", "E", "c"}:
            value = self.cstring()
            return value.decode("utf-8") if tag != "c" else {"bytes_size": len(value)}
        if tag in {"w", "d"}:
            return self.take(1).decode("latin1")
        if tag in {"l", "q", "i", "I"}:
            value = self.varint()
            return -value if tag in {"q", "I"} else value
        if tag in {"g", "G"}:
            value = 0
            for _ in range(self.varint()):
                value = (value << 31) + self.varint()
            return -value if tag == "G" else value
        if tag == "f":
            return struct.unpack("<d", self.take(8))[0]
        if tag in {"Z", "M", "Q"}:
            return {"special": self.take(1)[0]}
        if tag in {"T", "L", "S", "P"}:
            return self.many(self.varint())
        if tag == "D":
            count = self.varint()
            keys, values = self.many(count), self.many(count)
            return dict(zip(keys, values, strict=True))
        if tag in {"v", "b", "B", "X"}:
            value = self.take(self.varint())
            return value.decode("utf-8") if tag == "v" else {"data_size": len(value)}
        if tag in {":", ";"}:
            return {"parts": self.many(3)}
        if tag == "C":
            flags = self.varint()
            name, line, args, count = self.one(), self.varint() + 1, self.one(), self.varint()
            if flags & 1:
                self.one()
            if flags & 2:
                self.varint()
            if flags & 4:
                self.varint()
            return {"code_name": name, "line": line, "args": args, "argc": count}
        raise ValueError(f"不支持的常量标签 {tag!r}")


def permitted(value: Any) -> bool:
    if isinstance(value, str):
        if value in FIELDS:
            return True
        # 仅保留已识别的接口地址；内置 Key、Bearer 字符串等不在白名单内。
        if value.startswith("https://"):
            parts = urlsplit(value)
            return parts.hostname in HOSTS and not parts.username and not parts.password
    if isinstance(value, list):
        return bool(value) and all(permitted(item) for item in value)
    if isinstance(value, dict):
        return set(value) == {"count", "hcaptcha"} or value.get("Host") == "store.steampowered.com"
    return False


def analyze(path: Path) -> dict[str, Any]:
    sample = path.read_bytes()
    outer = pefile.PE(data=sample)
    payload, payload_offset = resource(outer, 27)
    main, files, raw_size = payload_files(payload)
    inner = pefile.PE(data=main)
    blob, blob_offset = resource(inner, 3)
    expected_crc, length = struct.unpack_from("<II", blob)
    if length != len(blob) - 8 or zlib.crc32(blob[8:]) != expected_crc:
        raise ValueError("常量容器长度或 CRC 不匹配")
    modules = []
    evidence = []
    position = 8
    while position < len(blob):
        end = blob.index(b"\0", position)
        name = blob[position:end].decode("utf-8")
        size = struct.unpack_from("<I", blob, end + 1)[0]
        position = end + 5
        if position + size > len(blob):
            raise ValueError("模块大小越界")
        modules.append({"name": name, "size": size, "main_dll_offset": hex(blob_offset + position)})
        if name in {"__main__", "getmail"}:
            data = blob[position : position + size]
            reader = Constants(data)
            count = struct.unpack_from("<H", data)[0]
            values = []
            for index in range(count):
                offset = reader.position
                value = reader.one(values)
                values.append(value)
                if permitted(value):
                    evidence.append(
                        {
                            "module": name,
                            "index": index,
                            "main_dll_offset": hex(blob_offset + position + offset),
                            "value": value,
                        }
                    )
            if data[reader.position :] != b".":
                raise ValueError("模块常量未完整解析")
            modules[-1]["constant_count"] = count
        position += size
    return {
        "sample_sha256": hashlib.sha256(sample).hexdigest(),
        "sample_size": len(sample),
        "format": "PE32+ x86-64 / Nuitka KAY onefile / Zstandard / Python 3.10",
        "payload_offset": hex(payload_offset),
        "payload_size": len(payload),
        "uncompressed_size": raw_size,
        "file_count": len(files),
        "main_dll_size": len(main),
        "constant_resource_offset": hex(blob_offset),
        "constant_resource_size": len(blob),
        "modules": modules,
        "protocol_constants": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample", type=Path)
    parser.add_argument("--output", type=Path, default=Path("analysis/sample_report.json"))
    args = parser.parse_args()
    try:
        report = analyze(args.sample)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"静态分析完成：{report['file_count']} 个封装文件，报告写入 {args.output}")
        return 0
    except (OSError, ValueError, struct.error, pefile.PEFormatError, zstandard.ZstdError) as error:
        print(f"静态分析失败（{type(error).__name__}），请确认附件和依赖版本", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
