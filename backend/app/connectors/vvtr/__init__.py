"""VVTR（长尾猴量化）行情接入 —— WS v2 深度行情 + HTTP 兜底/历史K线

文档：https://doc.vvtr.com/
- 实时行情走 wss://rest.vvtr.com/v2/connect（type=14，国内期货 250~500ms 推送）
- HTTP REST 限频 1 次/秒（全局），仅作兜底与历史K线
"""
