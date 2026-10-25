# 第三批真实交付记录（2026-10-19）

## 状态

**三项工作包已实施；系统仍部分完成，未发布。** 本轮是执行代码，不是仅生成计划。对应子计划 `docs/superpowers/plans/2026-10-19-stock-completion-batch-03.md`。S04完整服务器确认、P01启动DDL、R02旧研究入口、D01及R03数据资源仍有未完成工作，不停止续做自动化。

## 实现与金融口径

### B03-1 分盘只读行情

- JWT生成的ExecutionContext显式传给报价/K线；模型参数不能把virtual改成live。live读新live键并兼容旧键；virtual只读自己的键，不调用供应商补数、不自动seed。
- 虚拟归档排除seed和未知source行，披露剔除数量；日期排序/相同记录去重，不同同时间记录冲突拒绝；OHLC、时间、成交量和报价数值校验。旧/相同时间forming不能改写已收盘bar；当前quote不覆盖虚拟历史收盘。
- 独立复查发现：现有写入器会将新浪补价和人工pulse写进virtual命名空间，且历史source=virtual已丢失原tick来源。**命名空间隔离不等于来源认证。** 因此新报价入口拒绝 `sina_virtual_fill`、pulse和未知来源；保留原始data_source。原写入器未被本批重写或清库。
- 归档本身不能追认证，真实披露 `source=virtual_namespace_cache`、`origin_verification=archive_tick_origin_not_preserved` 和“仅模拟展示、可能含历史脉冲、非真实市场/策略证据”。指标和模型系统上下文同步披露，不能称已恢复真实逐笔来源认证。没有可信当前quote时 `live_last_price=null`，不把历史close冒充当前价。
- 恢复的仅是 `get_quote/get_kline/calculate_indicator` 只读分盘链。主力/扫描/盘口等未适配工具仍关闭，聊天写仍关闭。股票数据未取自期货缓存，股票聊天仍409未开放。

### B03-2 本盘确定性指标

- MA/EMA/MACD/RSI/BOLL/KDJ/VOLUME通过本盘K线计算，不交给模型猜测；输出输入来源、mode、读取时间、forming标记及来源限制。
- virtual不足不读live补齐；缺成交量不补0算VOLUME；指标窗口不足返回 `insufficient_market_data`。复查特别修复VOLUME均量尚未形成却返回success的路径。
- 原确定性公式不变；本包未认证报价新鲜度、历史逐笔来源或完整S05预算/会话/记忆隔离。缓存新鲜度明确未认证。

### B03-3 A股价值因子

- PE/PB仅有限正值有资格；负值、0、NaN和±inf不会被中位数填充重新纳入分组或选股。资格掩码在去极值/填充前、回归前后和标准化后保持，单因子及多因子组合使用同一政策。
- 增长、动量等合法负值保留，不做“一刀切删负数”。市值中性化和市值加权仍按原有有效市值规则，不补造市值。
- 方法升级 `stock-research-2026-10-19.v4`；保存 `factor_policies`：政策版本、规则、日期×证券观测数、原始合格/非正/非有限/缺失/预处理后/填补数量。分析按输入区间计数，组合按请求区间计数；这些数不伪装实际成交或IC有效配对数量。
- 前端报告展示保存的政策及计数；旧报告缺字段显示“未记录”，保留原结果/方法版本。现有worker方法保护使旧v3排队任务明确method_changed失败409，不静默换算法执行；部署前须处理存量队列。
- 父R04还有其他金融边界，研究仍research_only；五年容量、PIT/不可变输入、基准、整手/停牌/涨跌停/完整费税未由本批解决。

## 修改文件

后端产品：

- `backend/app/services/ai/tools/market/quote_kline.py`
- `backend/app/services/ai/tools/market/scoped_cache.py`（新）
- `backend/app/services/ai/tools/indicator.py`
- `backend/app/services/ai/tools/registry.py`
- `backend/app/services/ai/tools/safety.py`
- `backend/app/services/ai/gateway.py`
- `backend/app/services/stock_factors/catalog.py`
- `backend/app/services/stock_factors/engine.py`
- `backend/app/services/stock_factors/METHOD.md`
- `backend/app/api/stock_factors.py`

前端：

- `frontend/src/lib/stock-api.ts`
- `frontend/src/lib/stock-research-api.ts`
- `frontend/src/components/factor-research/research-report.tsx`

验证：新增 `backend/tests/test_ai_read_market_scope.py`、`backend/tests/test_stock_value_policy.py`；更新 `backend/tests/test_stock_financial_selection.py`（只将明确方法版本断言v3升级v4）、`frontend/src/lib/stock-research-api.test.mjs`、`frontend/src/components/factor-research/research-report.test.mjs`。

复用/扩展隔离脚本 `scripts/test-stock-completion-batch-02-postgres.py`（可选扩展钩子，默认仍可独立运行；旧immutable JSON证据不改），新增 `scripts/test-stock-completion-batch-03-postgres.py`。无Git元数据，未提交/建分支。

## 验证证据

### 红绿与独立复查

- `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ai_read_market_scope.py -q` 首轮 **11 failed**，`logs/stock-completion-b03-market-red.log`。实现后发现测试把用户工具偏好Redis键也算成行情键、旧forming夹具OHLC自身冲突；修正夹具/明确只断言行情键，没有放宽“不读live”或“旧forming不改收盘”核心断言。
- 追加质量反例：损坏缓存2 fail→通过，`logs/stock-completion-b03-quality-red.log`；相同时间forming和空指标2 fail→通过，`logs/stock-completion-b03-boundary-red.log`；非法报价其他字段4 fail→通过，`logs/stock-completion-b03-quote-fields-red.log`。
- 复查来源和VOLUME反例 **6 failed、20 passed**（含新增来源披露断言），`logs/stock-completion-b03-review-red.log`；修复后受影响三个文件 **76 passed**，`logs/stock-completion-b03-review-green.log`。
- 价值政策分工初始32项red；最终60个新价值政策用例覆盖fillna/standardize开关、none/MAD/3sigma、联合中性化、单/多因子和请求区间错误优先级。目标八文件 **183 passed、32既有警告、54.06秒**；前端目标两文件 **14 passed**。独立股票复查重跑60项通过，未发现Important/Critical。
- 独立行情复查先发现上述两项Important，修复后重跑26项通过，确认无新增Important/Critical；明确仅复核分盘及来源披露，不认证真实逐笔数据或完整S05。

### 完整回归和真实本地链路

- 根目录 `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q --junitxml=logs/stock-completion-b03-backend.xml`：首轮 **2410 passed、3既有skipped、502 warnings、125.51秒**。此后新增复查修复，最终重跑结果另补，不能只引用旧全套当最终结果。
- **最终全后端** `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q --junitxml=logs/stock-completion-b03-backend-final.xml`：**2419 passed、3既有skipped、502 warnings、124.59秒**，`logs/stock-completion-b03-backend-final.log`。没有添加skip、删除旧测试或关闭类型检查；上述首轮与最终数字不可相加。
- 前端目录 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)`：**360 passed、0 failed/skip**，`logs/stock-completion-b03-node.log`。
- 前端目录 `node node_modules/typescript/bin/tsc --noEmit --incremental false`：退出0，`logs/stock-completion-b03-tsc.log`。
- 根目录 `backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-03-postgres.py`：真实PG私有schema、实际本地Redis的随机QA键、真实JWT和ASGI HTTP/SSE/gateway/registry/指标读取；新规则结果实际create→claim→finish→HTTP复开，其他用户/登录盘404。
  - 首轮验证脚本误调用harness内部嵌套解析函数，AttributeError；`logs/stock-completion-b03-pg-20261019-085134.json` 保存失败，PG和已建Redis QA键均清理。修脚本后通过，没改产品或降低断言。
  - 复查修复后的最新报告 `logs/stock-completion-b03-pg-20261019-090118.json`：**61检查全通过**，PG schema清理true，独有Redis QA键已清理，3账户余额/订单/流水未被聊天写改变。
  - 上游/付费模型调用0；provider为确定性本地替身，行情明确合成QA缓存，不把fixture或本机PG通过当真实行情来源认证。没有用mock替代本轮应验证的真实PG存储/状态/权限或Redis键读取。

## 兼容、成本与发布风险

- 无新依赖、DDL或生产迁移，无重导/混复权/回填价量；新增字段用现有结果JSON保存。
- 指标与行情新增keyword-only mode默认live兼容旧内部调用；严格数值/来源拒绝属于明确行为变更，旧脏数据不再报成功。未适配能力保持关闭，异常应回退到关闭，不回退到live补价。
- 资格与计数是线性面板运算；本批无上游费用。没有完成五年性能/峰值内存验收。
- 本批未重启带遗留DDL的原后端，未执行生产构建/浏览器人工/发布回退演练。因此只能报告源码及隔离验证结果，**不能说用户当前在线实例已加载修复**。

## 最终检查点（北京时间17:05）

三个计划子切片本机验证结束：后端2419通过/3既有跳过，Node360通过，TSC退出0，真实PG/Redis/HTTP61通过、隔离数据清理。两个独立审查域均复核通过，无本批未修复Important/Critical。系统总体仍部分完成，所有父包/发布/人工验收边界保持上文，不把通过数量当全功能认证。

## 人工验证路径与下一轮四包

发布安全路径验收后：

1. 分别登录live/virtual，在聊天读取同合约报价、K线、MA；检查mode、来源/时间和差异。virtual缺数据应明确拒绝，sina补价/pulse报价不能冒充观测价，旧归档来源限制常显。
2. VOLUME窗口大于现有bars时明确不足，历史bar不能被旧forming或当前quote改写；原手工模拟资金/下单入口仍保留。
3. 新建PE/PB研究，在保存报告展开政策和计数；负PE/PB股票不入样；复开旧报告显示原结果和“未记录”，不能回填成v4。
4. 用另一用户/另一登录盘验证研究报告404；本批测试不能代替全站所有菜单验收。

下一轮：①S04持久确认/写幂等完整切片；②P01启动DDL版本化和安全发布检查；③R02旧研究入口归一/队列复现；④D01盘后增量/缺口/恢复状态。若追求virtual真实观测认证，另需改生产者保留原tick来源并分离pulse、受控历史迁移，不能靠读取器追认。
