# B33 本地连接恢复：本机运行验证通过，完整产品仍部分完成

时间：2026-10-23 06:16（上海）。三个工作包对应独立兼容计划`docs/superpowers/plans/2026-10-23-stock-preview-connector-recovery.md`。本包不改A股/期货生产功能、金融公式或共享运行配置，解决B32原Sina连接未启动的运行缺口。本人浏览器、完整交易/研究/AI、正式发布与回退验收未完成。

## 问题、最小兼容方案与实际文件

原`backend/app/connectors/sina/main.py`固定0.0.0.0，原API无认证且有手动monthly全合约采集入口，依赖容器内网边界。直接重用本地旧启动spec会把该接口绑定Windows全部网卡；未核实防火墙，不启动该入口。不是将容器内网设计本身判为漏洞，而是不能无条件照搬宿主部署。

不修改冻结原入口/API/guard/后台循环，新增`backend/scripts/stock_preview_connector.py`：原configure_environment加载既有配置，要求127.0.0.1:6380、无远端副本、8110配置，然后原Uvicorn factory固定127.0.0.1:8110。客户端原本地URL不变，Docker原路径不受影响，不提供任意host/port参数，不操作AI/订单/账户。新增`backend/tests/test_stock_preview_connector.py`、`scripts/check-stock-b33-preservation.py`及本包计划/交付；运行spec仅在`logs/stock-b33-loopback-connector.spawn.json`，无密钥。

调用必须从backend目录用`python -X utf8 -m scripts.stock_preview_connector`，不是从仓库根直接运行脚本文件；spawn spec已指定目录。模块启动没有新认证绕过或金融算法，不替换原create_app生命周期。loopback接口仍按原API信任本机环境，并非正式公网认证方案。

## 实际验证与失败

Python为`backend/.venv/Scripts/python.exe -X utf8`；除spec指定的启动目录外，命令在仓库根。

- 新测试缺模块6失败，真实红日志`logs/stock-b33-launcher-red.log`；新增实现后7目标测试通过，含正向环境/错误Redis/远端副本/端口漂移、原factory与固定loopback参数。环境逐例清理，不用外部变量掩盖错误原因。
- 原connector/API/guard/客户端/行情聚合、价格降级、AI盘隔离和历史市场隔离先跑68通过/1warning：`logs/stock-b32-connector-readonly-regression.log`。
- `python -m pytest backend/tests/test_stock_preview_connector.py backend/tests/test_sina_bridge_api.py backend/tests/test_sina_bridge_loop.py backend/tests/test_sina_bridge_protection.py backend/tests/test_sina_client.py backend/tests/test_price_fallback_sina.py backend/tests/test_sina_kline_30m.py backend/tests/test_sina_kline_aggregate.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py backend/tests/test_full_local_backend.py -q`：78通过/1既有Starlette警告，`logs/stock-b33-regression.log`；最后环境隔离补充后同命令复验结果见`logs/stock-b33-regression-final.log`。此为风险相关回归，不是全后端/真实供应商验证。
- 确认8110不监听且不存在其他bridge进程后，`python backend/scripts/local_service.py spawn logs/stock-b33-loopback-connector.spawn.json`退出0；原spawn使用CREATE_NO_WINDOW。实际父20708、监听37348，netstat只有127.0.0.1:8110，没有0.0.0.0/IPv6全网卡监听，没有强停他人。
- 使用httpx trust_env=False、5秒超时依次GET本地`/health`、`/stats`、`/quotes`，全部200；health redis/last_poll_ok true、last_poll_age6.7、replica_enabled false，缓存67条。`logs/stock-b33-connector-http.json`。未调用手动monthly、单合约quote或日线/分钟K线端点，不为测试发起额外上游请求。
- 经真实3001前端→原8002→真实Redis GET `/api/market/quotes`、`/api/market/channel-status`、`/api/market/session-status`，3个200，`logs/stock-b33-market-http.json`。默认未登录模式公共行情缓存67条，含2026-10-15/21/22混合日期；轮询成功不证明所有品种今天有交易或已更新，不能用采集时间改写交易日期。未读取/构造本人JWT，不是本人账户或下单验收。
- `python scripts/verify-ux-runtime.py`退出0、HTTP9通过，AI仍1任务paused/user，原main研究/EOD/归档worker true、GP false、release_verified false；`logs/stock-b33-runtime.log`。没有付费模型请求，最后普通AI仍provider_rate_limited/cost_state unknown。
- B32原guard第一次退出1，1071库存仅新增launcher，后来再新增独立保护脚本后旧差异只有2声明新增、无改/删；不是旧1070全库存原样。`logs/stock-b33-preservation.log`保留失败，`logs/stock-b33-source-audit.json`证明原1070文件及B32四原股票源码SHA未变。没有改允许清单制造旧guard通过。
- 新无白名单B33基线含1082文件，在新增实现后建立（不是开工前证明），snapshot/verify退出0；`logs/futures-preservation-b33-start.json`。原保护证据以上述B32差异和四源SHA为准；新基线只保护后续续接。

新启动逻辑经实际Redis/HTTP和原前后端连接验证；mock仅用于启动参数及原细粒度异常测试，不以mock代替真实运行。没有frontend/生产源码变化，未重复TSC/构建和无差别全回归；B32已存构建继续使用，未做新发布认证。浏览器工具仍不可用，没有新截图或本人页面验收。

## 金融、成本与回退

0金融公式、单位、复权、原行情Schema、数据版本迁移变更，无DDL或Teajoin重导。恢复的是原后台Sina quote/月度循环，实际会采集上游并更新原Redis缓存；不能说整轮零来源调用/零副作用/全部价格独立认证。既有循环频率/guard不变，上游请求总量及间接费用未独立计量。未开启远端复制、不新增Teajoin/模型请求，不恢复用户暂停AI，不接真实账户。

本机运行回退：先核对当前父/子PID与精确模块命令和归属，仅停止本包自有bridge；保留缓存/数据库和原容器/进程，不回滚整库或删除pid文件，不停止主后端和系统默认服务。原生产模块字节未变，无数据库向后兼容迁移。当前是恢复后可验证的loopback边界，未实施停机/重启故障演练或正式生产回退。

人工路径：3001登录后期货行情只读查看缓存交易日和K线，区分未开盘/历史数据/接口失败；不要启动AI或下单作为探活。切A股进入B32固定版本核对，确认版本/证券/单位与未认证限制。该本人验收当前未执行，不能保证期货全部功能或AI对话成功。

## 下一四包

1. 本人浏览器只读验收B32核对、双市场与期货K线；最多一次有明确超时预算的行情端点验收，失败留证不循环调用。
2. A股缺口原因/授权/请求费用预算，复用原证据，不盲补采、不过早认证最新/五年行情。
3. 版本化股票只读Agent、引用/工具Schema/幂等/费用固定评测；供应商429未解除不无限探活，未完成入口继续关闭。
4. 可信历史/PIT/复权锚点、五年/基准/回放扩池和正式发布/回退/容量安全人工验收；共享功能另兼容包，不动期货原生产功能。自动化保持。
