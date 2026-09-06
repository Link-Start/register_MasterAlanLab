"""Tavily/Auth0 请求的统一节点重试与代理轮换信号。"""

from __future__ import annotations

import time
from urllib.parse import urlparse


TRANSIENT_HTTP_STATUSES = {408, 425, 500, 502, 503, 504}


class RetryControlSignal(RuntimeError):
    """重试执行器向业务编排层发送的控制信号。"""


class ProxyRotationRequired(RetryControlSignal):
    """当前出口 IP 应立即废弃，由批处理层更换 Session 和代理。"""

    def __init__(self, reason: str, *, node: str = "", cause=None):
        super().__init__(reason)
        self.reason = reason
        self.node = node
        self.cause = cause


class NetworkNodeFailed(RetryControlSignal):
    """未配置代理时，节点的网络重试次数已经用完。"""

    def __init__(self, reason: str, *, node: str = "", cause=None):
        super().__init__(reason)
        self.reason = reason
        self.node = node
        self.cause = cause


def _node_name(method: str, url: str) -> str:
    try:
        parsed = urlparse(str(url))
        target = f"{parsed.netloc}{parsed.path}" if parsed.netloc else str(url)
    except Exception:
        target = str(url)
    return f"{method.upper()} {target}"


def external_request_with_retry(
    request_func,
    url: str,
    *,
    node: str | None = None,
    max_attempts: int = 3,
    retry_delay: float = 2.0,
    **kwargs,
):
    """对不经过 Tavily 代理的辅助服务请求执行独立三次重试。"""
    name = node or _node_name(getattr(request_func, "__name__", "REQUEST"), url)
    max_attempts = max(1, int(max_attempts))
    retry_delay = max(0.0, float(retry_delay))
    for attempt in range(1, max_attempts + 1):
        try:
            response = request_func(url, **kwargs)
        except Exception as exc:
            if attempt >= max_attempts:
                raise
            delay = retry_delay * (2 ** (attempt - 1))
            print(
                f"    [辅助请求重试] {name} 失败，{delay:g} 秒后重试 "
                f"({attempt}/{max_attempts - 1}): {exc}"
            )
            if delay:
                time.sleep(delay)
            continue

        status = getattr(response, "status_code", None)
        if status in TRANSIENT_HTTP_STATUSES and attempt < max_attempts:
            delay = retry_delay * (2 ** (attempt - 1))
            print(
                f"    [辅助状态重试] {name} 返回 HTTP {status}，"
                f"{delay:g} 秒后重试 ({attempt}/{max_attempts - 1})"
            )
            if delay:
                time.sleep(delay)
            continue
        return response


class RetrySession:
    """
    包装 requests/curl_cffi Session。

    每个 HTTP 节点最多执行 ``max_attempts`` 次；每次传输异常都会累计到
    当前出口 IP。累计达到 ProxyManager 的阈值后抛出轮换信号，由上层用新
    IP 和新 Session 恢复当前账号。
    """

    def __init__(
        self,
        session,
        *,
        proxy_manager=None,
        max_attempts: int = 3,
        retry_delay: float = 2.0,
        default_timeout: float = 30.0,
    ):
        self._session = session
        self.proxy_manager = proxy_manager
        self.max_attempts = max(1, int(max_attempts))
        self.retry_delay = max(0.0, float(retry_delay))
        self.default_timeout = max(0.1, float(default_timeout))

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        return self._session.close()

    def request(self, method: str, url: str, **kwargs):
        node = _node_name(method, url)
        kwargs.setdefault("timeout", self.default_timeout)
        request_func = getattr(self._session, method.lower(), None)
        if request_func is None:
            request_func = lambda request_url, **request_kwargs: self._session.request(
                method, request_url, **request_kwargs
            )

        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = request_func(url, **kwargs)
            except Exception as exc:
                last_error = exc
                rotate = False
                if self.proxy_manager is not None:
                    rotate = self.proxy_manager.record_network_failure(node, exc)

                if rotate and self.proxy_manager.proxy_api_url:
                    reason = (
                        f"当前出口 IP 累计网络失败达到 "
                        f"{self.proxy_manager.max_network_failures_per_ip} 次"
                    )
                    self.proxy_manager.request_rotation(reason)
                    raise ProxyRotationRequired(
                        reason, node=node, cause=exc
                    ) from exc

                if attempt >= self.max_attempts:
                    reason = f"节点 {node} 连续 {self.max_attempts} 次网络请求失败"
                    if self.proxy_manager is not None and self.proxy_manager.proxy_api_url:
                        self.proxy_manager.request_rotation(reason)
                        raise ProxyRotationRequired(
                            reason, node=node, cause=exc
                        ) from exc
                    raise NetworkNodeFailed(
                        reason, node=node, cause=exc
                    ) from exc

                delay = self.retry_delay * (2 ** (attempt - 1))
                print(
                    f"    [网络重试] {node} 失败，{delay:g} 秒后重试 "
                    f"({attempt}/{self.max_attempts - 1})"
                )
                if delay:
                    time.sleep(delay)
                continue

            status = getattr(response, "status_code", None)
            if status == 429:
                reason = f"节点 {node} 返回 HTTP 429"
                if self.proxy_manager is not None and self.proxy_manager.proxy_api_url:
                    self.proxy_manager.request_rotation(reason)
                    raise ProxyRotationRequired(reason, node=node)
                return response

            if status in TRANSIENT_HTTP_STATUSES:
                if attempt < self.max_attempts:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    print(
                        f"    [状态重试] {node} 返回 HTTP {status}，"
                        f"{delay:g} 秒后重试 ({attempt}/{self.max_attempts - 1})"
                    )
                    if delay:
                        time.sleep(delay)
                    continue
                raise NetworkNodeFailed(
                    f"节点 {node} 连续 {self.max_attempts} 次返回 HTTP {status}",
                    node=node,
                )

            return response

        # 循环逻辑保证不会到这里，仅用于静态检查和异常兜底。
        raise NetworkNodeFailed(
            f"节点 {node} 请求失败", node=node, cause=last_error
        )

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)
