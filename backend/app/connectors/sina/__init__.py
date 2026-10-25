"""新浪行情源接入 —— 独立桥容器（qihuo-sina-bridge）

新浪 HTTP 接口（hq.sinajs.cn 实时行情 + stock2.finance.sina.com.cn K 线）
的唯一出网点。复用 backend 镜像、以 `python -m app.connectors.sina.main`
启动，数据经两条路分发（与 vvtr 源同契约）：

- Redis 源键 `market:live:source:sina:quote:{sym}`（TTL 7d，带 recv_ts）：
  backend merge 层读源键合并，不再直连新浪；
- HTTP 服务（默认 8110，仅 compose 内网）：backend 的 K 线主源
  （kline_provider）、AI 工具 quote 兜底、运维脚本按需拉取。
"""
