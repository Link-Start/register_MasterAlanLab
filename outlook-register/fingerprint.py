from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import secrets
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from PIL import Image

EDGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
)


@dataclass(slots=True)
class BrowserFingerprint:
    uaid: str
    user_agent: str = EDGE_USER_AGENT
    sec_ch_ua: str = '"Microsoft Edge";v="147", "Not.A/Brand";v="8", "Chromium";v="147"'
    sec_ch_ua_platform: str = '"Windows"'
    sec_ch_ua_platform_version: str = '"19.0.0"'
    canvas: str = ""
    webgl: dict[str, object] | None = None
    url_dfp: str = ""


def random_uaid() -> str:
    return secrets.token_hex(16)


def random_birth_date() -> date:
    year = random.randint(1980, 2005)
    month = random.randint(1, 12)
    day = random.randint(2, 25)
    return date(year, month, day)


def random_base64_image(width: int = 300, height: int = 150) -> str:
    """Matches fpgen.generate_random_base64_image's PNG data URL shape."""
    pixels = bytes(random.randrange(256) for _ in range(width * height * 3))
    image = Image.frombytes("RGB", (width, height), pixels)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def webgl_data() -> dict[str, object]:
    return {
        "webgl_unmasked_vendor": "Google Inc. (NVIDIA)",
        "webgl_unmasked_renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0)",
        "webgl_vendor": "WebKit",
        "webgl_renderer": "WebKit WebGL",
        "version": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
        "shading_language_version": "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
        "depth_bits": 24,
        "max_vertex_attribs": 16,
        "max_varying_vectors": 30,
        "max_vertex_uniform_vectors": 4095,
        "max_combined_texture_image_units": 32,
        "max_texture_size": 16384,
        "max_cube_map_texture_size": 16384,
        "max_renderbuffer_size": 16384,
        "max_viewport_dims": {"0": 32767, "1": 32767},
    }


def build_fingerprint(
    *,
    uaid: str | None = None,
    user_agent: str = EDGE_USER_AGENT,
    timezone: int = 480,
    language: str = "en-US",
) -> BrowserFingerprint:
    uaid = uaid or random_uaid()
    canvas = random_base64_image()
    webgl = webgl_data()
    # fpgen emits a compact query-like urlDfp value. Keep field names and order.
    fonts_hash = hashlib.md5("Arial,Times New Roman,宋体,Courier New".encode()).hexdigest()
    canvas_hash = hashlib.md5(canvas.encode()).hexdigest()
    encoded_webgl = quote(json.dumps(webgl, separators=(",", ":")), safe="")
    url_dfp = (
        "bua=" + quote(user_agent.replace("Mozilla/", ""), safe="")
        + "&os=Win32&lproc=8&ol=true&rtt=4&chrm=true&prosub=20030107&eval=33"
        + "&appv=147.0.0.0&ls=true&dm=8&mtp=0&nc=82&pr=1&sr=1920x1080"
        + f"&scd=24&asr=1.0&tz={timezone}&dst=0&tzo={timezone}&bl={quote(language, safe='-')}"
        + f"&fh={fonts_hash}&fn={quote('Arial,Times New Roman,宋体', safe='')}&lh={canvas_hash}"
        + "&dr=https%3A%2F%2Fsignup.live.com%2F&w=1920&id=" + uaid
        + "&a=signup&c=" + encoded_webgl
    )
    return BrowserFingerprint(
        uaid=uaid,
        user_agent=user_agent,
        canvas=canvas,
        webgl=webgl,
        url_dfp=url_dfp,
    )
