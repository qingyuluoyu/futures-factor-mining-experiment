"""路线 A 教练 prompt 纯函数"""

from __future__ import annotations

from typing import Any


def build_coach_messages(
    symbol: str,
    timeframe: str,
    bars_n: int,
    base_population: int,
    base_generations: int,
    base_mutation_p: float,
    base_crossover_p: float,
    has_seed: bool,
) -> list[dict[str, Any]]:
    """构造教练 system/user 消息"""
    system = (
        "你是期货因子遗传规划搜索教练。只输出一个 JSON 对象，不要 markdown。\n"
        "字段仅允许：\n"
        '  population: 整数 10-100\n'
        "  generations: 整数 3-50\n"
        "  mutation_p: 浮点 0.05-0.8\n"
        "  crossover_p: 浮点 0.2-0.9\n"
        "  use_seed_tokens: 布尔，是否沿用上一轮种子\n"
        "  note: 简短中文说明（≤80字）\n"
        "禁止输出因子 token 或交易指令。目标是提高样本外稳健性，避免过拟合。"
    )
    user = (
        f"品种={symbol} 周期={timeframe} K线数={bars_n}\n"
        f"当前建议基准: population={base_population}, generations={base_generations}, "
        f"mutation_p={base_mutation_p}, crossover_p={base_crossover_p}\n"
        f"是否已有种子因子: {'是' if has_seed else '否'}\n"
        "请给出下一轮搜索参数 JSON。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
