# A 股日线回放模拟盘实施契约

执行状态：四包已实施，真实迁移重复执行 / 校验、授权三快照导入、QA并发HTTP及浏览器回放验证完成。当前范围与未完成边界见 `docs/STOCK-REPLAY-VERIFICATION-2026-10-17.md`；不代表既有研究 / AI链路通过。

用户已确认：先做日线回放撮合，初始模拟资金可自行输入；不接真实资金账户。按现有模块化单体实现，不复制期货保证金 / 卖空规则。四个工作包：原始数据快照与版本迁移；确定性撮合 / 账户服务 / 权限幂等；同布局前端账户资金订单持仓；真实链路与回归验证。

## 金融边界

- 撮合使用供应商原始未复权日线，不使用现有前复权研究库作为成交价格。
- 当前先开放沪深主板普通 A 股（代码 60xxxx / 00xxxx）。不把科创板、创业板、北交所规则混成统一百股规则。
- 在已收盘交易日提交指令，下一已知交易日的开盘价模拟成交；推进后展示该交易日收盘估值。回放图只返回游标日及以前的数据。
- 百股买卖、禁止裸卖空、T+1、买入开盘涨停拒绝、卖出开盘跌停拒绝、有限成交量参与率；日线无法还原盘口或排队，采取保守规则，不声称真实可成交。
- 佣金为账户明确保存的模拟假设（默认万三、最低5元）；印花税 / 过户费按执行日期的方法版本计算，滑点默认千分之一。全部金额使用 Decimal，货币到分。
- 数据缺失不当作停牌；缺少官方交易日、原始价、复权因子或涨跌停数据时拒绝入库。跨复权因子变化的交易日暂不推进，明确提示未支持公司行动，不伪造分红送转。
- 多股票账户使用固定版本快照。数据导入走有界 CLI，不在 HTTP 请求里下载历史。第一批用用户已授权 DaoShare 接口导入 000001 / 600036 / 600519 的近期原始数据，不全市场抓取。

## 分工与文件边界

数据包独立维护 `backend/app/models/stock_replay.py`、`backend/app/services/stock_replay_data.py`、`backend/migrations/20261017_001_stock_replay.sql`、`backend/scripts/migrate_stock_replay.py`、`scripts/import-stock-replay-data.py` 及数据测试。模型使用独立 `ReplayBase` metadata，不进入现有启动时 Base.create_all；新增结构只能由版本迁移创建。DDL 事务执行、带 advisory lock 和版本记录、重复执行安全；旧表不改，不删除历史数据。

主流程维护 `backend/app/services/stock_replay_engine.py`、`backend/app/services/stock_replay.py`、`backend/app/schemas/stock_replay.py`、`backend/app/api/stock_replay.py`、main/config 接线及业务测试。

前端包独立维护 `frontend/src/lib/stock-replay-api.ts`、`frontend/src/components/trading/stock-replay-workspace.tsx`，以及 trading / positions / orders / assets / dashboard 页面股票分流和 market-routes 开放；保留原期货组件。原图表由主流程增加不复权、成交量单位 props，前端包不改核心图表。

## 数据模型（字段名是实现契约）

所有主键 UUID，时间 UTC aware，数据日期 date，金额 Numeric(18,2)，价格 Numeric(18,4)。ORM JSON 字段通过业务 Schema 校验。

1. `StockReplayDataset` / `stock_replay_datasets`：id、version(String64唯一)、code(String6)、instrument_id(String16如000001.SZ)、name(String32)、start_date、end_date、bars_json(JSONB)、meta(JSONB)、created_at。
   - bars_json 是升序数组：`{trade_date:"YYYY-MM-DD",open:"11.00",high:"12.00",low:"10.00",close:"11.50",volume_shares:100000,adj_factor:"100.01",up_limit:"12.65",down_limit:"10.35"}`。
   - meta 包含 supplier=daoshare、market=stock、currency=CNY、timezone=Asia/Shanghai、frequency=1d、adjustment=none、volume_unit=share、fetched_at、calendar_dates(官方开市日期数组)、source_apis、schema_version=1、授权边界说明。version 是标准化数据与来源元数据中稳定部分的 SHA256；不因 fetched_at 重复产生版本。
2. `StockReplayAccount` / `stock_replay_accounts`：id、user_id(FK users)、trading_mode(String16)、name(String64)、initial_cash、cash、realized_pnl、fees_paid、start_date、current_date、end_date、datasets_json(JSONB code→dataset UUID string)、positions_json(JSONB默认[])、settings_json(JSONB)、status(String16 active/completed)、revision(Integer默认0)、create_key(UUID)、params_hash(String64)、rules_version(String40默认stock-replay-v1)、created_at、updated_at。唯一(user_id,trading_mode,create_key)，索引(user_id,trading_mode,created_at)。positions_json为`[{code,quantity,cost_per_share:"10.001",acquired_on:"YYYY-MM-DD"}]`。
3. `StockReplayOrder` / `stock_replay_orders`：id、account_id(FK)、idempotency_key(UUID)、params_hash(String64)、code(String6)、direction(String8 buy/sell)、quantity(Integer)、order_type(String8 market/limit)、limit_price(价格nullable)、status(String16 pending/filled/rejected/cancelled)、reason(String160 nullable)、submitted_on(date)、filled_on(date nullable)、filled_price(价格nullable)、fees(金额默认0)、cash_delta(金额nullable)、realized_pnl(金额默认0)、reserved_cash(金额默认0)、created_at、updated_at。唯一(account_id,idempotency_key)，索引(account_id,status,created_at)。
4. `StockReplayStep` / `stock_replay_steps`：id、account_id(FK)、idempotency_key(UUID)、kind(String16 advance/cancel)、from_revision(Integer)、params_hash(String64)、result_json(JSONB)、created_at。唯一(account_id,idempotency_key)和(account_id,from_revision)。日志用于推进与撤单的幂等响应。

业务服务对账户加 SELECT FOR UPDATE；先校验幂等键和输入摘要，再校验 expected_revision，避免重复付费 / 重复推进 / 重复交易。不同请求重复使用同一键返回409。订单状态只能 pending→filled/rejected/cancelled。账户到数据末日进入 completed；不能越界推进。任何失败必须回滚，不吞异常或返回默认零。

## HTTP 契约

全部 `/api/stock/replay/*` 需要 AuthContext，从 JWT 取 user_id / trading_mode，账户 ID 必须二者匹配，否则404。Feature Flag `STOCK_REPLAY_ENABLED` 默认false，未迁移 / 未启用时503，不暴露可操作的半成品。

- GET `/availability`：`{enabled:boolean,datasets:[{id,code,instrument_id,name,start_date,end_date,version,source,adjustment:"none"}]}`，最多100条最新快照元数据，不返回未来价格。
- GET `/accounts?limit=20&offset=0`：账户简表数组，用户与盘隔离，limit≤100。
- POST `/accounts`：`{idempotency_key:UUID,name:string,initial_cash:decimal,start_date:date,dataset_ids:UUID[1..20],commission_rate:decimal=0.0003,min_commission:decimal=5,slippage_rate:decimal=0.001}`。初始资金100至10亿元，不支持创建后覆盖资金或抹除历史；新试验新建账户。
- GET `/accounts/{id}?code=000001`：完整 `ReplayView`。
- POST `/accounts/{id}/orders`：`{idempotency_key:UUID,expected_revision:int,code:string,direction:"buy"|"sell",quantity:int,order_type:"market"|"limit",limit_price:decimal|null}`，返回最新 ReplayView。
- POST `/accounts/{id}/orders/{order_id}/cancel`：`{idempotency_key:UUID,expected_revision:int}`，返回 ReplayView。
- POST `/accounts/{id}/step`：同撤单 body，推进一个交易日，返回 ReplayView。重复相同请求不再次推进。

`ReplayView` 字段：id、name、market_type="stock"、trading_mode、method_version、price_adjustment="none"、currency="CNY"、initial_cash、cash、available_cash、reserved_cash、equity、total_pnl、fees_paid、realized_pnl（金额均字符串）、current_date、start_date、end_date、status、revision；positions 数组 `{code,name,instrument_id,quantity,available_quantity,cost_price,last_price,market_value,unrealized_pnl}`；orders 数组 `{id,code,direction,quantity,order_type,limit_price,status,reason,submitted_on,filled_on,filled_price,fees,cash_delta,realized_pnl}`；instruments 数组 `{code,name,instrument_id}`；chart `{code,adjustment:"none",volume_unit:"share",bars:[{time,open:number,high:number,low:number,close:number,volume:number}]}`（只到游标，最多250根）；data_versions 字符串数组。

创建 / 下单 / 推进 / 撤单均返回 ReplayView。下单 / 撤单 / 推进都会递增 revision，前端每次用最新版本；按钮禁用不是幂等机制。限价仅在后续日开盘满足条件时成交，不用当日最高最低反推盘中路径。

## 前端验收

股票“交易”采用原期货布局的账户摘要、K线、委托 / 持仓和下单区；新建回放账户可填写资金 / 起始日期、选择已验证数据快照，切换已有账户不重置资金。买入 / 卖出按“股”，清楚显示当前回放日期与“推进下一交易日”，未推进前订单 pending。assets / positions / orders 只读同一股票回放数据，不读取原 /api/paper。股票未配置数据时说明导入边界，绝不放回期货账户兜底。

## 必须验证

先失败测试，再实现：原始数据标准化和拒绝缺失；逐日现金 / 持仓 / 净值独立对照；下一开盘成交；T+1、禁卖空、百股、涨跌停、滑点与费用、限价与撤单；资金不足与成交量限制；公司行动和缺数据失败回滚；幂等与版本冲突；跨用户 / 跨登录盘404；迁移执行两次不破坏数据；真实供应商快照和 QA 账户完整回放；前端类型检查 / 构建 / 浏览器。

只对独立 QA 用户做资金和交易测试，不修改原用户持仓；不调用模型。开启新功能前执行迁移与数据导入，再设置本机 Feature Flag。回退只关闭 Flag / 恢复代码，新表保留，旧代码不依赖新表；不通过整库恢复覆盖用户新数据。
