# 执行19 P2：共享股票K线日期视窗、指标与容器高度

状态：已实现待验证。本包定向40/40通过；未启动服务、构建、执行迁移、连接PG或调用外部源/模型。统一TSC/构建由根整合，真实HTTP、本人浏览器和发布未验收。沿19计划02:10续做修订，保留18之后未知在建20变化/8新增，不回退、不认证或计为本包成果。

实际修改`frontend/src/components/market/stock-kline-chart.tsx`、`stock-kline-reference.test.mjs`；新增同目录`stock-chart-window.ts`、`stock-chart-window.test.mjs`。`stock-chart-boundary.test.mjs`已备份并回归，字节未改。目录与renderer分别由指标agent和根独占，本包只消费其约定，不改adapter/store/API/金融公式/期货/共享配置或已有页面差异。

## 控件与数据流

1. 日期为**视窗**，不截断金融计算输入：完整已载入日线仍经原prepare→日/周/月聚合→原指标与adapter送入renderer；纯resolver只验证真实日期、开始≤结束并解析已载入周期K的from/to与真实交集日期。给renderer新增可选`visibleDateRange`，由根对接dataZoom，原数组保持完整。近几天视窗不会重算EMA/RSI起点、删除MA60前缀或改变量比的此前N周期窗口。周/月K明确按已合成K线的最后交易日归属，未改聚合方法。
2. 开始/结束输入限制在已载入原始日期范围内；手动越界、周末无交集或倒序不会造K线，无交集显示可恢复空态、非法范围显示明确错误。默认信息栏取窗口结束对应真实index，预热数为该index+1，不是所选窗口长度。回放仍使用既有服务端按cursor截断的bars，不增加未来输入。
3. 日期范围只在当前bars对象及标的/复权/单位/涨跌参考语义下生效，同代码换源/换数据集当帧不沿用旧窗口。没有写入store、localStorage或跨用户日期缓存。“显示全部已载入日期”清日期并显式选all；原“复位K线”清日期并恢复60根；其他用户旧偏好未自动重置。
4. 一键“全部六指标”与“价格+成交量”只调用原setChartPreferences修改六个布尔字段，保留周期、十字线、显示根数及量比偏好。六组定义消费新增只读目录：MA/BOLL/VOL/MACD/RSI/KDJ；原量比1–20周期继续可选。未接通分时、分钟和异动仍禁用，没有虚构其它无限指标。
5. 新增逐线预热披露：摘要显示截至所指周期根数及不足条数，展开显示每条线现有/所需根数、缺几根；MACD标明34根只是EMA预热参考、不保证收敛；量比明确不足、零历史均量或无效量。数量门槛不认证数值有效/PIT/历史完整，没有将缺值填0。
6. 通过DOM ref、最近有界overflow祖先和控件真实高度计算canvas请求高度，ResizeObserver监听容器和控件，窗口resize只触发重新测量，不再依赖window.innerHeight估算。请求高320–680px，既有renderer保持主图至少240px并为固定副图增加总高，根/详情外层可继续纵向滚动。放大使用固定工作区，Esc及监听器/observer清理保留。

所有共享StockKlineChart入口自动使用本次控件：选源个股详情（行情/筛选等已有入口）和日线回放交易页，无须复制图表或修改调用方。未更改小数价量、股票源优先级、复权/涨跌参考、回放股/行情手单位、计算方法版本或费用。

## 验证和一次自审

先红：纯工具3项因尚未实现而失败，见`logs/stock-chart-workspace-19-window-red.log`；新增5个控件行为红、旧6项绿，见`...-ui-red.log`；补充显示全部/最近60复位语义2项红，见`...-reset-red.log`。实现后联合40项全部通过、0失败/跳过，见`logs/stock-chart-workspace-19-green.log`。

在`frontend`实际执行：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-chart-window.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-kline-reference.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-kline-reference.test.mjs src/components/market/stock-chart-window.test.mjs src/components/market/stock-chart-boundary.test.mjs src/components/market/stock-chart-data.test.mjs src/components/market/stock-quote-data.test.mjs src/components/market/stock-chart-indicators.test.mjs
```

新增测试核窗口后完整70根及MA60前缀保持、实际end对应68根预热、未来/空/非法窗口、换数据集即刻清范围、六指标与精简只改目标偏好、全部日期/最近60复位，以及模拟实际容器700→400px的ResizeObserver测量/销毁。原样本标准差、首价EMA/Wilder/KDJ固定向量、未来追加前缀稳定、价格参考/小数成交量及图表错误手动恢复继续回归。单元测试的DOM尺寸使用受控节点，不能当本人浏览器视觉证据。renderer真实ECharts日期视窗与生命周期由根另测，不冒领本包测试数量。

一次定向自审读取本包精确diff、目录实际契约及全部StockKlineChart调用方，确认日期未参与prepare/aggregate/adapter指标输入、源切换不会被旧窗口困住、无越回放cursor造数；与根明确新挂载不能重放旧zoomRevision覆盖日期视窗。未在本包运行TSC/构建/在线验证，不以控件测试代替生产发布门禁。

## 保护、兼容、成本与人工路径

字节备份`logs/stock-chart-workspace-19-backup/`与修改前SHA `logs/stock-chart-workspace-19-before.json`；最终本包4文件SHA `logs/stock-chart-workspace-19-accepted-sha.json`；精确差异（含新增文件）`logs/stock-chart-workspace-19-final.patch`。根统一接受清单前，不覆盖18接受基线或未知差异清单。回退只按本包hunk；有后续修改时不整文件覆盖。

旧调用方不传日期仍按原日/周/月、根数与偏好运行，renderer新字段可选。无迁移/新依赖/新数据请求；新增外部数据与模型费用0，指标仍复用原计算。根TSC/构建、真实来源/历史资格、本人滚动与发布仍待验收。

人工路径（未执行）：获准恢复当前源码预览后，股票行情打开已有日线证券→选日期前后核对同一根MA60/EMA/RSI/均量不变→检查非法/周末无交集恢复→同代码切数据源确认日期清空→全部六指标/精简及展开逐线预热→窄屏/面板高度变化/放大Esc，滚至KDJ与底部说明；进入回放交易页，日期最大值应为当前cursor，不能显示未来日线。

下一轮3包：根统一renderer生命周期/真实SVG、全前端/TSC及当前源码构建；获准原服务/HTTP与本人行情/回放视觉验收；来源候选与历史资格独立推进，不因控件齐全声称历史、可成交回测或ML产品完整。
