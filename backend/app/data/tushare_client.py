"""Tushare 兼容数据客户端 —— DaoShare 代理（teajoin.com/debug/api）

与 Tushare HTTP 协议一致：POST {api_name, token, params, fields}，
返回 {code, msg, data:{fields, items, has_more}}。
基于 httpx（项目现有依赖），内置限速与重试。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://teajoin.com/debug/api"


class TushareError(RuntimeError):
    """代理返回的业务错误（含 Tushare code/msg）"""


@dataclass(frozen=True, slots=True)
class TusharePage:
    """单次响应记录及供应商分页声明；has_more=None 表示完整性未认证。"""

    records: list[dict[str, Any]]
    has_more: bool | None


class TushareClient:
    """Tushare HTTP 客户端（限速 + 重试）"""

    def __init__(
        self,
        token: str,
        timeout: float = 60.0,
        max_retries: int = 4,
        min_interval: float = 0.2,
    ) -> None:
        if not token:
            raise TushareError("Tushare token 为空（.env 的 TUSHARE_TOKEN）")
        self._token = token
        self._timeout = timeout
        self._max_retries = max_retries
        self._min_interval = min_interval
        self._last_call = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Content-Type": "application/json"},
            follow_redirects=True,
        )

    def query(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str = "",
    ) -> list[dict[str, Any]]:
        """调用一个接口，返回 items 转成的 dict 列表（可能为空）"""
        data = self._query_data(api_name, params, fields)
        return self._records(data)

    def query_page(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str = "",
    ) -> TusharePage:
        """保留 has_more 声明，不将缺失的完整性元数据视为完整结果。"""
        data = self._query_data(api_name, params, fields)
        has_more = data.get("has_more")
        if has_more is not None and not isinstance(has_more, bool):
            raise TushareError(f"{api_name}: has_more 必须为布尔值或缺失")
        return TusharePage(records=self._records(data), has_more=has_more)

    @staticmethod
    def _records(data: dict[str, Any]) -> list[dict[str, Any]]:
        columns = data.get("fields") or []
        return [dict(zip(columns, item)) for item in (data.get("items") or [])]

    def _query_data(
        self,
        api_name: str,
        params: dict[str, Any] | None,
        fields: str,
    ) -> dict[str, Any]:
        payload = {
            "api_name": api_name,
            "token": self._token,
            "params": params or {},
            "fields": fields,
        }

        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            self._throttle()
            try:
                resp = self._client.post(f"{BASE_URL}/{api_name}", json=payload)
                resp.raise_for_status()
                result = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                # 网络错误 / 响应截断 / JSON 解析失败 —— 退避重试
                last_err = exc
                time.sleep(0.8 * (attempt + 1))
                continue

            code = result.get("code")
            if code == 0:
                return result.get("data") or {}
            # 业务错误：限流/积分不足等，退避重试
            last_err = TushareError(f"{api_name}: {result.get('msg')}")
            time.sleep(0.8 * (attempt + 1))

        raise TushareError(f"{api_name} 重试 {self._max_retries} 次后失败: {last_err}")

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        """最小调用间隔，避免触发代理限流"""
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()


_client: TushareClient | None = None


def get_client() -> TushareClient:
    """进程内单例（token 来自 .env 的 TUSHARE_TOKEN）"""
    global _client
    if _client is None:
        from app.core.config import load_settings

        settings = load_settings()
        _client = TushareClient(settings.tushare_token)
    return _client
