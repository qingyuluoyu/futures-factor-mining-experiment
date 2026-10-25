"""调研结果预渲染 Markdown —— 强制具体点位，减少模型模棱两可"""

from __future__ import annotations

from app.services.ai.research.types import ProductSignal


def _cell(value: float | None) -> str:
    """表格单元格数字。"""
    if value is None:
        return "-"
    if abs(value) >= 100:
        return f"{value:.2f}"
    if abs(value) >= 10:
        return f"{value:.3f}"
    return f"{value:.4f}"


def _row(sig: ProductSignal) -> str:
    """单行信号。"""
    return (
        f"| {sig['product_code']} | {sig['symbol']} | {sig['direction_zh']} | "
        f"{_cell(sig.get('last_price'))} | {_cell(sig.get('entry'))} | "
        f"{_cell(sig.get('support'))} | {_cell(sig.get('resistance'))} | "
        f"{_cell(sig.get('stop_loss'))} | {_cell(sig.get('take_profit'))} | "
        f"{sig.get('risk_reward') if sig.get('risk_reward') is not None else '-'} | "
        f"{';'.join(sig.get('reasons') or [])[:40]} |"
    )


def build_report_markdown(
    long_list: list[ProductSignal],
    short_list: list[ProductSignal],
    range_filtered: int,
) -> str:
    """生成主代理应优先照抄的操作表。"""
    header = (
        "| 品种 | 合约 | 方向 | 现价 | 入场点 | 支撑位 | 压力位 | 止损 | 止盈 | 盈亏比 | 依据 |\n"
        "|------|------|------|------|--------|--------|--------|------|------|--------|------|"
    )
    lines: list[str] = [
        "以下为技术面筛选后的**可操作点位**（震荡已过滤）。",
        "请原样用表格汇报数字，禁止改成模糊表述（如「附近」「酌情」「观望为主」）。",
        "",
    ]

    lines.append(f"## 做多（{len(long_list)}）")
    if long_list:
        lines.append(header)
        for sig in long_list:
            lines.append(_row(sig))
    else:
        lines.append("（无明确做多品种）")
    lines.append("")

    lines.append(f"## 做空（{len(short_list)}）")
    if short_list:
        lines.append(header)
        for sig in short_list:
            lines.append(_row(sig))
    else:
        lines.append("（无明确做空品种）")
    lines.append("")

    lines.append(
        f"震荡/趋势不明已过滤：{range_filtered} 个。"
        "点位基于日线均线与近 20 根高低，仅供参考，不构成投资建议。"
    )
    return "\n".join(lines)
