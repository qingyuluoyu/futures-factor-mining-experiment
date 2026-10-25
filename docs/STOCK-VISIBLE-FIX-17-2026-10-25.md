# 执行17 股票列表、个股视窗与稀疏日线修复

状态：已实现待验证。定向前端60/60与TSC通过；当前服务停止，本包未构建、启动服务、连接PG或进行本人浏览器验收。不以SSR或组件夹具代替真实滚动/页面验收。

## 实际修改与数据流

限定9文件（5个股票组件、4个直接测试）：

- `frontend/src/components/market/stock-board-browser.tsx`：StockMembers不再占剩余flex高度/被挤缩；表格自然行高，标准/紧凑模式最小宽40/32rem，仅表格横向滚动，外部页面负责纵向滚动。保留桌面六行业卡、50条分页、10000 offset边界与取消旧请求。
- `frontend/src/components/market/stock-universe.tsx`：搜索列表外层保持至少20rem及可滚动视窗。
- `frontend/src/components/market/stock-quote-detail.tsx`：详情唯一纵向滚动所有者；各tab自然高度，移除tab内的第二层整页滚动。保存经Schema校验的可选coverage，在图表/覆盖tab展示所选本地源总根数、日期、已载入条数、请求/有效上限、本地窗口状态及历史完整性未核验。读完本地来源明确不代表供应商没有更早历史，旧响应缺coverage不伪造总量。
- `frontend/src/components/market/stock-kline-chart.tsx`：图表/容器不再以h-full、flex-1裁剪内容；内容自然高，显式height为最小高度，底部显示实际载入根数，放大仍支持Esc。
- `frontend/src/components/market/tick/echarts-candlestick.tsx`：蜡烛、成交量和MACD柱最大宽12px；主图最少240px，画布下限包含8px顶距、22px日期轴、所有副图信息栏/固定高度/间距。开启四副图时增加画布高度，底部通过详情纵向滚动可达。
- `stock-members-workflow.test.mjs`、`stock-quote-detail.test.mjs`、`stock-kline-reference.test.mjs`、`tick/adapter.test.mjs`：覆盖布局与旧交互回归、coverage兼容、实际ECharts SVG及柱几何。

只读核对参考项目`F:/111/tickflow-stock-panel-main/frontend/src/components/StockDailyKChart.tsx`与`EChartsCandlestick.tsx`的主副图高度累计逻辑。本包没有改参考项目、共享样式、期货组件、认证、网关、运行配置、数据库或金融公式。

金融口径不变：仍使用真实本地输入、既有价格参考/不复权与前复权隔离、原指标方法/缺值策略。605058本库只有5根盘后日线的事实属于数据包调查，本包不增加蜡烛或补造历史。coverage为同轮数据包提供的可选向后兼容字段；UI不将本地读尽升级为历史完整认证。

## 验证与自审

原始红测试`logs/stock-visible-fix-17-red.log`为26项22通过4失败。中断后续跑24通过2失败，后两项系新测试节点遍历假设（table同时有空态children、包含财务表的祖先被误排除）；修正遍历后补充20rem最小高红例，证据`logs/stock-visible-fix-17-resume-red.log`。新增coverage先红见`logs/stock-visible-fix-17-coverage-red.log`，失败原因是页面未展示所选源覆盖。

在`frontend`实际执行：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-members-workflow.test.mjs src/components/market/stock-quote-detail.test.mjs src/components/market/stock-kline-reference.test.mjs src/components/market/tick/adapter.test.mjs src/components/market/stock-board-browser.test.mjs src/components/market/stock-chart-data.test.mjs src/components/market/stock-quote-data.test.mjs src/components/market/stock-chart-boundary.test.mjs src/lib/stock-kline-contract.test.mjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

结果60/60、0失败/跳过；TSC exit0，证据`logs/stock-visible-fix-17-green.log`及`logs/stock-visible-fix-17-tsc.log`。稀疏日线测试将真实组件SSR高度送入真实ECharts，检查240px主图、所有grid位于canvas内及实际candle几何宽度不超过12px。全前端、生产构建、HTTP和本人视觉未在本包执行，由根整合或待许可后验证。

一次定向自审已检查本包相对原始字节备份的精确diff、所有调用方布局、旧响应与财务慢/失败独立恢复、取消晚到响应、原涨跌参考和指标测试。未发现新增致命金融风险；剩余风险是浏览器实际flex/滚动与新部署资源尚未验收，不能称已完成并验证。

## 保护、兼容、成本与人工路径

原始字节备份与SHA：`logs/stock-visible-fix-17-backup/`、`logs/stock-visible-fix-17-before.json`；续做coverage前另存`logs/stock-visible-fix-17-resume-backup/`、`logs/stock-visible-fix-17-resume-before-coverage.json`。最终9文件SHA：`logs/stock-visible-fix-17-accepted-sha.json`；精确差异：`logs/stock-visible-fix-17-final.patch`。根接受总清单前不得覆盖旧保护清单。回退限于以上文件对应diff；若已有后续修改，按hunk回退，不全文件覆盖新修改。

无迁移、无API破坏、无新增依赖；新增外部源/模型费用0。画布增加仅为已选副图必要像素，数据读取次数、根数与计算方式均不增加。

人工路径（未执行）：恢复获准的当前源码预览后，以股票身份进入行情→全部股票/行业成分，下滚到第50只和分页；Ctrl+K列表验证竖向与横向滚动、点击标的；打开605058，确认显示真实5根和本地源覆盖日期/未核验说明；切换四副图、滚至KDJ与底部口径说明，检查蜡烛不再过宽；切估值/覆盖tab、窗口缩至窄屏、放大图表/Esc，再返回原列表。

下一轮与根整合的3包：当前源码统一构建及旧产物回退；获准服务/真实HTTP与本人视觉验收；数据覆盖缺口按供应商文档、授权及显式预算独立推进。此文不授权服务重启或补采。
