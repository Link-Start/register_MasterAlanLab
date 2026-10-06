from __future__ import annotations

import html
import re
import secrets
import string
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlencode, urlsplit

STEAM_ORIGIN = "https://store.steampowered.com"
VERIFY_PATH = "/account/newaccountverification"
PINYIN_NAMES = tuple(
    """
    QingFeng XingHe LiuYun ChenXi WanXing MuYu ShanHe YunShu
    YeHang YueBai QingZhou TingFeng ZhiXia ChuYang SongYue ZhuYing
    MingYue HanXing LuoXue LanTing ChenLu XingYu FeiYu QiuYue
    ChunShan XiaHe DongXue QiuLan YunHai YunFan YunQi YunShan
    YunZhou YunLin QingHe QingShan QingYun QingLan QingChen QingMu
    QingXia QingYe BaiHe BaiYu BaiYun BaiFan BaiXue BaiShan
    BaiYan GuYu YuShui XiaoMan MangZhong LiChun LiXia LiQiu
    LiDong BaiLu HanLu ChuShu XiaoXue DaXue AnHe AnRan
    AnNing AnYu YuAn QingYu QingChuan YuChuan XiZhou XiYue
    JiangYue JiangFeng JiangNan NanShan NanFeng NanZhi NanXi BeiChen
    BeiYu BeiHe XiShan XiFeng XiYun DongLi DongShan DongFeng
    ZiMo ZiYan ZiYun ZiChen MoLin MoYu MoRan MoZhu
    ShuYing ShuYu ShuYue TingYu TingLan TingXue TingYue QianShan
    QianFan YunTian XingChen XingChuan XingLan XingFan XingYun YueHua
    YueYing YueShan YueHe FengYu FengHe FengLin FengYue LingXi
    LingYun LingShan ZhiYuan ZhiYu ZhiQiu ZhiLan LinXi LinYu
    LinHe LinYue CangLan CangShan CangHai CangYun YuLin YuZhou
    """.split()
)


def is_steam_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname == "store.steampowered.com"
            and parts.port in {None, 443}
            and not parts.username
            and not parts.password
        )
    except ValueError:
        return False


def verification_url(url: str, creation_id: str) -> str:
    url = html.unescape(url.strip())
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    tokens = query.get("stoken", [])
    ids = query.get("creationid", [])
    if (
        not is_steam_url(url)
        or parts.path != VERIFY_PATH
        or parts.fragment
        or len(tokens) != 1
        or not re.fullmatch(r"[a-fA-F0-9]+", tokens[0])
        or ids != [str(creation_id)]
        or not str(creation_id).isdigit()
    ):
        raise ValueError("验证链接必须来自 Steam 官方域名，并匹配本次 creationid")
    return f"{STEAM_ORIGIN}{VERIFY_PATH}?{urlencode({'stoken': tokens[0], 'creationid': ids[0]})}"


def extract_verification_link(body: str, creation_id: str) -> str | None:
    body = html.unescape(body)
    for url in re.findall(
        r'https://store\.steampowered\.com/account/newaccountverification\?[^\s<>"\']+', body
    ):
        try:
            return verification_url(url, creation_id)
        except ValueError:
            continue
    return None


def mail_texts(raw: bytes) -> list[str]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    texts = []
    for part in message.walk():
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        if part.get_content_disposition() == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if payload:
            charset = part.get_content_charset() or "utf-8"
            try:
                texts.append(payload.decode(charset, errors="replace"))
            except LookupError:
                texts.append(payload.decode("utf-8", errors="replace"))
    return texts


def random_account_name(prefix: str = "") -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_]{0,48}", prefix):
        raise ValueError("ACCOUNT_PREFIX 只能含字母、数字、下划线，最多 48 位")
    return f"{prefix}{secrets.choice(PINYIN_NAMES)}{secrets.randbelow(10000):04d}"


def random_password() -> str:
    characters = [
        secrets.choice(alphabet)
        for alphabet in (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%*_-")
    ]
    alphabet = string.ascii_letters + string.digits + "!@#$%*_-"
    characters.extend(secrets.choice(alphabet) for _ in range(14))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)
