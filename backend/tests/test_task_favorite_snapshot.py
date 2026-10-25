"""任务收藏快照 JSON 安全 —— Numeric 列的 Decimal 进 JSONB 会 500"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

from app.api.ai_trading_favorites import _jsonable


def test_jsonable_converts_decimal_recursively() -> None:
    data = {
        "capital_usage_min_pct": Decimal("12.30"),
        "capital_usage_max_pct": Decimal("100.00"),
        "nested": {"rules": [Decimal("1.5"), 2, "x"]},
    }
    out = _jsonable(data)
    assert out["capital_usage_min_pct"] == 12.3
    assert out["nested"]["rules"][0] == 1.5
    # 直接可 JSON 序列化（修复前抛 TypeError: Object of type Decimal）
    json.dumps(out)


def test_jsonable_converts_uuid_and_passthrough() -> None:
    uid = uuid4()
    out = _jsonable({"id": uid, "name": "任务", "n": None, "b": True})
    assert out["id"] == str(uid)
    assert out["name"] == "任务"
    assert out["n"] is None
    assert out["b"] is True
