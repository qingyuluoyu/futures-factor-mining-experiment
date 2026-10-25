# 执行19 P3：股票六指标目录与绘制验证

状态：已实现待真实验证。按`docs/superpowers/plans/2026-10-25-stock-chart-completion.md`及02:10边界修订，只新增独立指标目录/测试，补充adapter直接测试。不修改未知在建20变化/8新增，不把本包overlay当仓库接受清单。服务停，未启动PG/HTTP/浏览器/worker，未构建/迁移/外呼/安装依赖。

三个工作包：对照本地tick实际指标并固定目录契约；数量预热/量比资格只读函数；固定向量和真实ECharts SVG验证。控件agent消费目录，root负责日期renderer和整合。本包没有改wrapper、chart-data公式、adapter生产代码、renderer、store或期货。

实际文件：新增`frontend/src/components/market/stock-chart-indicators.ts`及`.test.mjs`；修改`frontend/src/components/market/tick/adapter.test.mjs`（原4项全部保留，追加4项）。其它文件不在本包修改范围。

## 导出契约和方法

`STOCK_CHART_INDICATORS`为冻结的六项目录；条目key/id/label/title/lines，逐线id/label/requiredBars/matureBars。`stockIndicatorWarmup(availableBars)`接收**已清洗、已聚合**图表的根数，返回目录及availableBars，逐线增加available/missingBars/mature/missingMatureBars。负值、非安全整数、null等明确拒绝，不把无效根数当0。数量可用只说明初始化所需数量足够，不证明每个数值有效、完整交易日历、PIT、历史授权或策略资格。

|key / id|逐线要求|
|---|---|
|showMA / ma|MA5/10/20/60：各需5/10/20/60根|
|showBOLL / boll|上/中/下轨：均需20；中轨id为ma20，复用既有字段|
|showVolume / vol|VOL柱1；VOL5/10为5/10，包含本根|
|showMACD / macd|DIF/DEA/柱均1根可初始化；34=26+9−1仅界面预热参考，不保证EMA收敛|
|showRSI / rsi|RSI6/14/24需7/15/25根，价差数比收盘价数少1|
|showKDJ / kdj|K/D/J需9根，高低价差0仍不可算，不因数量够就补0|

`STOCK_INDICATOR_METHOD_VERSION`直接等于原`STOCK_CHART_METHOD_VERSION`，仍为stock-chart-1.0.0。没有更换MACD首价EMA种子/2倍柱、KDJ首个有效RSV、Wilder RSI平价50、BOLL样本标准差或均量口径。MACD的mature字段只是上述根数参考的机器可读状态，title已明确不保证收敛；不能解释成统计/交易成熟认证。

`stockVolumeRatioAvailability(bars, days)`接收已聚合有效图表数据和1..20整数周期，返回requiredBars=N+1、availableBars、missingBars、available、value（number|null）、reason（available/insufficient_bars/invalid_volume/zero_reference_volume）。仅调用原stockVolumeRatios对最后N+1根计算；本根量0可得0，历史N根均量0不可算，null/NaN/负量拒绝，不前填、不补零。不是盘中量比，不制造分钟数据。

参考只读：`F:/111/tickflow-stock-panel-main/frontend/src/components/EChartsCandlestick.tsx`的SUB_CHARTS只有vol/macd/rsi/kdj、OVERLAY_INDICATORS只有boll，MA由showMA控制；对应本项目移植renderer同样六项加量能比较。未把不存在的指标、分时/分钟或异动算作交付，未改外部tick代码或导入263根候选。

## 验证与真实失败

frontend目录实际执行：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-chart-indicators.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-chart-indicators.test.mjs src/components/market/tick/adapter.test.mjs src/components/market/stock-chart-data.test.mjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

目录先红6/6失败，原因是新模块尚不存在；实施后6/6通过。追加adapter验证后联合30/30通过（目录6、原计算16、adapter8），无跳过。新增固定向量验证MA终值77/74.5/69.5/49.5、BOLL样本方差35、RSI100、KDJ90及MACD标准参数分数值，原0.01成交量未截断；追加未来数据不改变前缀，缺周期仍null，平价KDJ不可算、RSI50、BOLL平价等原方法保持。

真实ECharts SVG SSR（现有已装依赖）逐项核19个现有series的输入值：蜡烛、4条MA、2条BOLL上下轨、VOL柱+5/10均量、3条MACD、3条RSI、3条KDJ；BOLL中轨复用MA20，不虚增第20条序列。SVG实际生成path，模型每条序列70点，无NaN/undefined/Infinity。另实际渲染1根/25根平价零量样本，空KDJ/RSI保留断点，零均量不生成量比标签。不是仅通过字符串检查声称绘图成功，也不等同本人浏览器交互验收。

全项目TSC真实失败：`src/components/dashboard/stock-dashboard.tsx(49,3): TS1005 '}' expected`，属于root已记录的未知在建修改，本包不修、不接受、不以测试绿覆盖。已向root报告，按指令不重跑全量TSC/构建。

为独立检查新目录及真实类型依赖，在仓库根执行：

```powershell
node frontend/node_modules/typescript/bin/tsc --project logs/stock-chart-indicators-19-tsconfig.json
```

该日志内QA配置extends原frontend/tsconfig，只选新目录，noEmit/incremental=false，不改共享配置。首次因配置处于logs目录未找到现有Node types而失败；typeRoots指向已有frontend/node_modules/@types后退出0，未安装包、跳类型或放宽生产断言。这只证明目录相关依赖类型通过，不替代全项目TSC。

未执行后端/原PG/HTTP/本人浏览器/完整构建/发布/迁移，无新增模型、数据调用或外部成本。目录/量比函数为纯本地小规模运算；量比只计算最后N+1根，不重复全历史计算。无接口/数据库/认证兼容变更，已有绘制公式不变。

## 保护与剩余工作

修改前备份`logs/stock-chart-indicators-19-backup/`与before-sha.json保留原adapter测试及只读公式/adapter/renderer初始字节。自审时确认只改授权adapter测试，生产chart-data/adapter/renderer当时均与初始SHA一致；随后renderer由root负责，本包不再把其后变化当违规回退，也不使用旧SHA覆盖root。

本轮三文件overlay为`logs/stock-chart-indicators-19-overlay-sha.json`，精确patch为`logs/stock-chart-indicators-19-changes.patch`。一次自审检查六开关/逐线阈值、MACD参考限制、量比零分母、小数/缺值、只复用原公式、SVG实绘、无越权文件修改；三文件现冻结。root负责整合接受清单，未知在建差异继续单列。回退先核SHA及后改，仅撤本包新目录和追加测试；无数据回滚。

人工路径（获准部署当前代码后）：打开5根真实日线，核MA5可用但MA10/20/60不足、MACD可算但预热参考不足、RSI/KDJ/BOLL不补零；切周/月按聚合根数提示；开全部六项核主副图与量比，零历史均量不显示假数；日期窗口/回放游标不得带入未来数据。这些本人验收尚未执行。

下一轮三个工作包：目录与控件/日期renderer统一回归；未知在建修改由原责任方修复并完成全TSC/构建；获准原服务本人交互和发布回退验证。不能据指标目录交付认证全历史、可成交回测或系统完整。
