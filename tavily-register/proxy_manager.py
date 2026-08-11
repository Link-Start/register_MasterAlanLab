"""代理提取与 IP 轮换管理模块"""

from __future__ import annotations

import re
import time
import requests


class ProxyManager:
    """
    代理 IP 管理类：
    - 支持通过 PROXY_API_URL 提取代理 IP (文本格式, 如 1.2.3.4:8080)
    - 针对单个 IP 限制最多 10 次注册尝试
    - 达到 10 次注册尝试后，每隔 30 秒轮询获取一次代理 IP
    - 检测到代理 IP 发生变化后，立即再次开启新一轮注册（最多 10 次），直到设定的总注册数量完成
    """

    def __init__(
        self,
        proxy_api_url: str | None = None,
        max_attempts_per_ip: int = 10,
        poll_interval: float = 30.0,  # 30 秒轮询一次
    ):
        self.proxy_api_url = (proxy_api_url or "").strip()
        self.max_attempts_per_ip = max_attempts_per_ip
        self.poll_interval = poll_interval

        self.current_proxy_url: str | None = None
        self.last_proxy_ip: str | None = None
        self.proxy_fetched_at: float | None = None
        self.attempts_on_current_ip: int = 0

    def fetch_proxy_from_api(self) -> tuple[str, str]:
        """
        发起 GET 请求从代理 API 提取代理地址

        Returns:
            (proxy_url, raw_ip)  例: ("http://198.51.100.1:8080", "198.51.100.1")
        """
        if not self.proxy_api_url:
            raise ValueError("未配置 PROXY_API_URL")

        resp = requests.get(self.proxy_api_url, timeout=15)
        resp.raise_for_status()
        text = resp.text.strip()

        # 提取 IP:PORT 格式
        match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b", text)
        if not match:
            raise ValueError(f"无法从响应提取 IP:PORT 格式: {text[:100]}")

        proxy_str = match.group(0)
        raw_ip = proxy_str.split(":")[0]
        proxy_url = f"http://{proxy_str}"
        return proxy_url, raw_ip

    def detect_exit_ip(self, proxy_url: str, timeout: float = 10.0) -> str:
        """
        通过代理发起 GET 请求到 http://ipinfo.io/json 检测实际出口 IP
        """
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
        headers = {
            "User-Agent": "curl/7.68.0",
            "Accept": "application/json",
        }
        resp = requests.get(
            "http://ipinfo.io/json",
            proxies=proxies,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        ip = str(data.get("ip", "")).strip()
        if not ip:
            raise ValueError(f"无法从 ipinfo.io 响应提取 IP: {resp.text[:100]}")
        return ip

    def get_proxy(self) -> str | None:
        """
        获取当前可用代理地址。
        若单 IP 已使用满 10 次，每隔 30 秒检测一次实际出口 IP，变动后立即开启下一次循环。
        """
        if not self.proxy_api_url:
            return None

        need_new_ip = (
            self.current_proxy_url is None
            or self.attempts_on_current_ip >= self.max_attempts_per_ip
        )

        if not need_new_ip:
            return self.current_proxy_url

        if self.current_proxy_url is not None:
            print()
            print("=" * 60)
            print(
                f"当前实际出口 IP ({self.last_proxy_ip}) 已尝试注册满 {self.attempts_on_current_ip} 次。"
            )
            print(f"暂停注册，每隔 {int(self.poll_interval)} 秒检测实际出口 IP 是否发生变化...")
            print("=" * 60)

        # 轮询获取新 IP，并通过 ipinfo.io 确认实际出口 IP 与上一轮不同
        while True:
            try:
                proxy_url, raw_ip = self.fetch_proxy_from_api()
                print(
                    f"  📡 提取到代理平台地址: {proxy_url}，正在通过 ipinfo.io 检测实际出口 IP..."
                )
                exit_ip = self.detect_exit_ip(proxy_url)

                if self.last_proxy_ip and exit_ip == self.last_proxy_ip:
                    print(
                        f"  ⏳ 检测到的实际出口 IP ({exit_ip}) 与上一轮相同，未发生变化，等待 {int(self.poll_interval)} 秒后重新检测..."
                    )
                    time.sleep(self.poll_interval)
                    continue

                self.current_proxy_url = proxy_url
                self.last_proxy_ip = exit_ip
                self.proxy_fetched_at = time.time()
                self.attempts_on_current_ip = 0
                print(
                    f"  ✅ 成功检测到实际出口 IP 变化: {exit_ip} (代理平台地址: {proxy_url})，开始新一轮注册！"
                )
                return self.current_proxy_url
            except Exception as e:
                print(
                    f"  ❌ 提取代理或检测实际出口 IP 失败: {e}，等待 {int(self.poll_interval)} 秒后重试..."
                )
                time.sleep(self.poll_interval)

    def record_attempt(self) -> None:
        """记录一次注册尝试（无论成功还是失败）"""
        if self.proxy_api_url and self.current_proxy_url:
            self.attempts_on_current_ip += 1
            print(
                f"  [代理统计] 当前出口 IP ({self.last_proxy_ip}) 已尝试注册 {self.attempts_on_current_ip}/{self.max_attempts_per_ip} 次"
            )
