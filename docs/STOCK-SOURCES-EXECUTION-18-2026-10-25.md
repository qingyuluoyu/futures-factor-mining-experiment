# 执行18 P1：明确日线来源的只读链路

状态：已实现待验证。目录API→严格客户端→详情选源→显式K线读取→来源/日期/覆盖展示已接通；33后端、30前端目标及TSC通过。原PG、新代码在线HTTP、本人页面及发布未执行，不能报生产验收完成。当前服务停，未拉起任何服务、worker或替代实例。

按`docs/superpowers/plans/2026-10-25-stock-pages-data-repair.md`的P1执行，承接执行17接受字节。三个工作包为本地来源目录/显式读取、详情接线与旧后端兼容、红绿验证与一次自审。不扩大到tick导入、研究计算、回放账本或其它agent文件。

## 实际改动与数据流

修改7个既有文件：

- `backend/app/api/stock.py`：新增Pydantic来源目录响应、只读`GET /api/stock/kline/sources`和`/kline`的source枚举；复用原历史统计、既有EOD读取与股票市场权限。显式失败保留request_id/no-store，不用另一来源填补。
- `backend/tests/test_stock_kline_reference.py`：新增真实ASGI+隔离SQLite factory的来源目录/显式选源/兼容/权限/禁用源读表边界。
- `frontend/src/lib/stock-api.ts`：旧函数第四个可选source参数，旧调用不传时请求URL保持原样。
- `frontend/src/lib/stock-kline-contract.ts`及`.test.mjs`：严格目录/回执校验、最近日期选择器、20秒超时、原HTTP会话保护及取消。已有coverage和旧响应兼容保留。
- `frontend/src/components/market/stock-quote-detail.tsx`及`.test.mjs`：新增来源选择及旧模式提示，复用原图表/财务/覆盖；切源独立加载，目录和K线晚响应不回写。

`backend/app/services/stock_eod.py`只备份未修改；EOD已有SQL投影、unknown/status筛选与250根上限均原样复用。没有新依赖、迁移、价格写入或财务口径变化。

## 契约与兼容

`GET /api/stock/kline/sources?code=NNNNNN`：

```json
{
  "catalog_version": "stock-kline-sources-v1",
  "code": "600001",
  "default_mode": "legacy_auto",
  "sources": [
    {"id":"history_qfq","source":"local_import","price_adjustment":"qfq","available":true,"reason":"available","available_bars":1,"first_date":"2026-10-15","last_date":"2026-10-15","window_cap":3000,"history_completeness":"unverified"},
    {"id":"eod_raw","source":"teajoin_eod","price_adjustment":"none","available":true,"reason":"available","available_bars":1,"first_date":"2026-10-16","last_date":"2026-10-16","window_cap":250,"history_completeness":"unverified"}
  ]
}
```

上例是测试契约示例，不是原PG真实证券行情。目录固定两个明确物理源；legacy_auto是旧选择规则，不作为第三份行情。reason为available/no_local_history/eod_disabled/instrument_not_found，不能把不可用都称无数据。EOD关闭时不读取EOD表，available_bars与日期为null，明确数量未知；目录未收录证券两源均不可用、数量0。后端12秒、前端20秒超时；聚合失败503，无空目录伪成功。沿用登录及`require_stock_market`，期货会话按既有协议409+X-Market-Capability=stock_required拒绝，不改共享权限契约。所有成功/读取错误都有request_id与no-store。

`GET /api/stock/kline?code=...&limit=...&source=...`：

|source|行为|
|---|---|
|缺省或legacy_auto|保留原history-first；旧历史为空且EOD开关启用才读盘后原价，旧返回字段保留|
|history_qfq|只读旧前复权历史，空源409+X-Kline-Error=no_local_history，不fallback|
|eod_raw|只读盘后原价；开关关闭503+source_disabled，不查询/绕开开关；空源409，不fallback|

显式请求的响应增加`selected_source`回执。客户端核对回执、source、price_adjustment及原coverage；旧后端忽略参数时不会将其结果当作已选源成功。目录与K线均校验6位ASCII证券，limit仍1..3000。显式证券未收录404；原缺省空证券响应兼容不变。

详情首开在目录中选最近last_date的可用明确来源，同日优先EOD原价；展示来源各自的末日和本地总数。用户可切回历史前复权；请求仍1500，EOD有效窗口仍250，图表近60/120等仍仅控制已载入显示。切源先清旧图/报价，重新读目录校验可用性，再读所选源；不触发数据下载、不拼接raw/qfq。用户选源在本详情实例内保持，重开/刷新按最新日期重新选择，不承诺跨浏览器保存。

旧后端目录404/405明确显示“旧模式”、历史优先限制及“重试来源目录”，首次允许沿旧接口读取；503/权限/格式/解析等错误停止，不降级为旧模式。用户已经手工选过来源后目录消失，停止并要求显式点击“使用旧模式”或重试，不能偷偷改读。成功空目录保留不可用原因并提示无开放本地来源，不当旧后端。切股/切源、关闭详情都取消原传输，晚目录不能触发旧证券K线，晚旧源报价不能覆盖新源。

## 实际命令与验证

backend目录：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_eod_integration.py tests/test_stock_market_api.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_eod_integration.py tests/test_stock_market_api.py tests/test_stock_eod.py -q
```

先红：目录尚未存在，1失败/8通过。实现后首次联合1失败/20通过，原因是新增测试假设股票市场拒绝403，而共享现有协议实际409；读取原`market_capability.py`后改为精确断言409+stock_required，未更改生产权限或放宽为任意拒绝。随后21通过；增加禁用EOD表缺失、未知证券等边界后最终33通过，无跳过。

这是现有测试体系的SQLite隔离fixture和FastAPI ASGI调用，未冒充原PG/在线HTTP。禁用表测试只删除测试内存库EOD表，不能照做生产库；其目的在于证明开关关闭时目录与显式拒绝路径不依赖EOD读取。

frontend目录：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/lib/stock-kline-contract.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-quote-detail.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-quote-detail.test.mjs src/lib/stock-kline-contract.test.mjs src/lib/stock-api.test.mjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

契约先红2失败/6通过，证明缺目录解析和source传参；UI先红3失败/13通过，证明缺最近源选择/旧模式/切源。实施后联合27通过，新增切股晚目录/无源目录/目录消失边界后最终30通过。TSC退出0。旧原价参考、小数手、复权隔离、滚动布局、财务独立加载、错误重试、旧响应与原API URL行为继续通过。

本包未执行全前端/构建，交root统一；新代码原PG、线上HTTP、本人页面、迁移/发布未执行。没有上游或模型调用，不把已有历史运行结果冒充本轮验收。

## 自审、成本与回退

一次精确差异自审已对照本包备份确认只改7项，stock_eod原字节不变；检查旧缺省优先级、显式请求不能fallback、EOD禁用读表、所选源回执、目录reason/schema、股票权限、取消和晚响应、财务请求独立及旧模式提示。没有改期货/共享认证/网关/布局/运行配置、AGENTS、总计划或总接受清单。生产代码和测试现已冻结。

字节备份`logs/stock-sources-18-backup/`，before-sha.json含8项；最终SHA为`logs/stock-sources-18-final-sha.json`（7变化、stock_eod不变），精确patch为`logs/stock-sources-18-changes.patch`。回退先核当前SHA与后续编辑，只撤本次source参数/目录/selector增量，不能整文件覆盖用户后改；新字段可选、旧接口仍可用，无数据库回滚。

每次图表首开/切源/手动重试增加一次本地目录GET和最多两项DB聚合，仍有既有时间边界；无新增远端/模型费用、依赖或缓存。原PG真实查询延迟及真实浏览器体验待验，不能据fixture证明生产性能。

金融公式、原价/复权算法、成交量手单位变化0。真实历史缺口仍在，605058本项目5根及tick候选263根结论未改；目录只披露既存范围，history_completeness固定unverified，不认证供应商全历史/授权或PIT，更不能据行情可见宣称回放、可成交回测或ML产品完整。

人工验证路径（服务按用户授权恢复并部署后）：股票用户从看板/板块打开同时有历史与EOD的证券，确认首选目录最新日期；切历史前复权核日期/来源同步改变且无拼接；开605058核仅真实5根，未编造tick历史；EOD关闭时选项未开放且不能强制读取；模拟旧后端404确认旧模式和重试；服务失败/切股/关窗核不遗留报价；财务页及原覆盖提示继续可用。

下一轮三个工作包：原PG/真实HTTP选源与性能验证；本人页面跨入口源/日期一致性验收及回退；候选历史来源证据与明确预算补齐。当前预算仍0，不据待办自行补采、迁移或恢复付费AI。
