# 执行17日线数据范围与本地历史核查

状态：已实现待验证。日线可选coverage后端与前端契约已接通并通过目标测试；原PG/HTTP/本人页面新代码验证未执行。当前服务已停止，不用13:27历史恢复记录代表当前运行，不自动重启、不另建替代实例。

本包三个工作包：核对605058本地已存数据与tick可复用资产；实现兼容的数据范围契约；验证与一次精确自审。未修改UI、期货、共享认证/网关/布局/运行配置、总计划或AGENTS。可见页面接线由visible分包负责，root统一验收。

## 实际数据证据

原PG5433的前任只读核查保留在`logs/stock-daily-fix-17-db-before.json`，本续做没有再次连接：605058澳弘电子在`kline_history`无行，现存585392根旧前复权日线只覆盖300只且不包含此票；有效EOD及observations均5根，日期2026-10-16、19、20、21、23。原服务函数limit=5/250/3000均只返回5根、has_more=false。这是本地所选来源读尽，不证明供应商只提供5根，也不证明上市以来历史完整。

`logs/stock-daily-fix-17-tick-local.json`记录tick项目已有605058.SH的263根本地Parquet，2025-08-11至2026-09-08、无重复日期，逐文件SHA已记录。这是可保留复用的候选资产；它尚未进入本仓库正式行情源。Parquet metadata仅ARROW:schema，不能凭文件存在认证数据来源或授权。

本续做定向只读核对tick的现行公开代码与配置（没有读取账号/密钥文件）：

- `data/data_sources/teajoin.yaml`的daily契约为`https://teajoin.com/daily`，POST，ts_code/start_date/end_date/trade_date；日期YYYYMMDD；vol映射volume且不缩放；amount乘1000。adj_factor单独定义为累计因子。
- `backend/app/data_providers/normalizer.py`明确归一化契约为价格元、成交量手、成交额元；不会按数值大小猜单位。`backend/app/services/kline_sync.py`对TickFlow原始请求使用adjust=none，其canonical列为symbol/date/OHLC/volume/amount，不保留逐行source或采集证据。
- `backend/app/jobs/daily_pipeline.py`现行流程可在官方日线未发布时调用Sina快照并flush_live_daily写同一日线存储；后续merge-upsert可覆盖。因此现行Teajoin配置不能反推263根历史文件当时全部来自Teajoin。
- `backend/app/indicators/pipeline.py`的enriched另行应用前复权；原始kline_daily与enriched不能互换。`repository.py`部分统一视图硬编码tickflow来源，也不能充当逐行来源证明。
- tick迁移计划要求逐行source/observed-at/version，但计划文字不是该263根实际执行证据。定向扫描kline_daily/cache未找到命名包含manifest/provenance/license/metadata的可用旁证；不据此断言其它位置绝无证据。

正式复用仍缺：逐行或不可变批次实际供应商与接口、acquired_at/collected_at、原响应hash/源版本、明确raw/qfq与因子锚点、单位版本、交易日历及停牌/上市日规则、授权范围。当前代码声明的元/手/元只证明现行设计，不能追认历史文件采集过程；文件mtime不能替代采集时间。

安全后续方案是先将263根文件以已记录SHA锁定为独立候选来源、保留raw单源、不拼EOD且不写旧qfq表；补齐或明确拒绝上述资格证据，再经独立兼容设计提供有权限的只读观察入口。没有证据时只能作为未认证观察资产，不能用于已认证回测/历史完整性结论。本包未创建该入口、未导入文件、未请求上游。

## 实现与契约

实际修改5个文件：

- `backend/app/api/stock.py`：在现有取源结果上附加coverage；本地旧qfq优先、无旧qfq才按既有开关读取EOD，原字段/复权/单位/涨跌参考不变。上海当日cutoff复用同一次取值；计数与返回数量/has_more冲突时走既有503、request_id和no-store，不发布矛盾范围。
- `backend/app/services/stock_eod.py`：只新增eod_coverage，复用同code/unknown/status/date筛选，DB内聚合count/min/max，不将全市场JSON拉进API内存，不修改原eod_bars函数或250窗口。
- `backend/tests/test_stock_kline_reference.py`：覆盖原价参考/小数手不变、qfq优先隔离、总数不是窗口数、空存储、请求3000但EOD有效250、同一日期/status/unknown/code过滤。
- `frontend/src/lib/stock-kline-contract.ts`及`.test.mjs`：coverage可选，旧响应继续通过；校验日期、数量、has_more、所选源窗口与历史资格，拒绝完整历史认证或供应商全量范围假声明。对实际请求核对requested_limit。

coverage字段：

|字段|契约|
|---|---|
|scope|固定selected_source_local_store，只统计本地所选来源|
|available_bars|非负整数，该来源当前可读取总条数|
|first_date/last_date|YYYY-MM-DD；0条时均null|
|requested_limit|原请求1..3000|
|effective_limit|qfq为原请求；EOD为min(请求,250)|
|window_status|window_limited / local_history_exhausted / no_local_history|
|history_completeness|固定unverified，不认证交易日历连续或上市以来完整|

无证券时保留原空响应、coverage缺省；不伪造来源。新前端能接受没有coverage的旧服务。没有数据库迁移、原数据写入或方法版本变更。仅增加所选源一次DB聚合查询，仍在原12秒超时内；PG真实延迟待验证。两个独立读取间发生追加/删除而数量口径冲突时拒绝该次响应，用户可重读，不吞错或填零。

## 实际执行与结果

在backend目录：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_eod_integration.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_eod_integration.py tests/test_stock_eod.py tests/test_stock_access.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_eod_integration.py tests/test_stock_eod.py tests/test_stock_market_api.py -q
```

第一轮红灯3失败/5通过，缺coverage及eod_coverage；实现后首次联合3失败/11通过，定位func导入遗漏并修复。第三条命令引用不存在test_stock_access.py，0测试执行，不能算通过。最后实际命令32通过，无跳过。这里是现有SQLite注入factory与FastAPI ASGI真实应用响应夹具，不等于当前原PG/线上HTTP验证；没有引导另一服务代替停机验证。

在frontend目录：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/lib/stock-kline-contract.test.mjs
```

先红2失败/4通过（coverage被旧Schema丢弃/缺冲突校验），实施后6通过。UI联合60及TSC由visible分包单独执行，root统一记录，不冒充本包亲自运行。未执行新代码原PG、在线HTTP、本人浏览器、全前端或构建；由root确认停机边界和统一回归。

一次自审对照`logs/stock-daily-fix-17-changes.patch`确认只改5个授权文件；检查原取源优先级/250边界/原单位/参考价/上海cutoff、unknown/status过滤、旧响应兼容和错误脱敏，未发现需扩范围的问题。生产代码现已冻结。

字节备份`logs/stock-daily-fix-17-backup/`及before-sha.json，最终5文件SHA为`logs/stock-daily-fix-17-final-sha.json`。回退仅对照字节备份和精确patch撤本次字段增量，先检查目标SHA，不能覆盖后续用户修改；无数据回滚需求。root负责总接受清单，未改总SHA。

新增数据/模型调用0，未付费训练、未迁移、未恢复AI任务。金融公式与复权口径变更0。剩余风险是原PG聚合性能及在线响应未验、263根历史来源证据不足；5根的真实缺口没有被本包填补，不报真实历史功能已完整。

人工路径（原服务按用户授权恢复并部署当前代码后）：以股票用户打开澳弘电子详情，确认5根日期与本地范围提示一致、历史完整性未核验；换已有qfq历史的证券确认source仍local_import且不拼EOD；核请求1与3000的条数/has_more/有效窗口提示；核旧后端无coverage时不编造总量；核失败请求有request_id且不残留旧详情。

下一轮三个工作包：原PG/线上coverage与查询延迟验证；tick候选来源不可变证据与授权补齐；显式预算下核Teajoin历史可获得区间及独立单源导入设计。上游预算仍0，不能以此待办自行补采。
