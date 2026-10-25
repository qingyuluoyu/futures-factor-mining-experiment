import re


def ensure_cny_futures_symbol(symbol: str) -> str:
    identifier = re.sub(r"[\s/_-]", "", symbol.strip().upper().split(".", 1)[0])
    if identifier in ("XAUUSD", "XAGUSD"):
        raise ValueError("国际贵金属使用美元报价，尚未启用独立美元模拟账户与已认证合约规则，禁止套用人民币期货默认规格")
    return symbol
