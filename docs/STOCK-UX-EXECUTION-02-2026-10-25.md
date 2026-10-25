# 第二轮：图表失败恢复、去重计算与定时续做

**已实现待验证**。用户要求全部未完任务持续执行，已把既有a-02-05/a-07-10更新为每天北京时间02:00/07:05，名称相应更新，状态ACTIVE、目标线程不变；更新后读取配置并用工具查看确认。没有重复创建或取消其它既有自动化。全功能队列见`docs/superpowers/plans/2026-10-25-full-system-delivery-queue.md`，沿原子包执行，不删除受阻验收项制造完成。

本轮三包：股票图表加载/渲染失败局部展示；手动重建加载器及边界并保留数据/周期/指标；删除移植后无用重复计算、运行验证和留续做记录。没有变更期货、共享认证/网关/布局/依赖、数据库或模型调用。

实际修改`frontend/src/components/market/stock-kline-chart.tsx`、`stock-kline-reference.test.mjs`；新增同目录`stock-chart-boundary.tsx`、`stock-chart-boundary.test.mjs`。计划/AGENTS仅更新状态和指针。

原dynamic loading组件忽略错误；不同Next加载实现可能把失败继续交给loading或抛给上层。现在局部StockChartLoading区分pending/failed，固定安全错误文案、不显示异常URL/堆栈；StockChartBoundary接住本图React渲染错误，不把普通图表异常扩散整页。明确手动重试才增加chartAttempt，重新创建失败的dynamic加载器及边界，保持既有bars/可见根数/指标/周期；不通过重试重取行情、发订单或调用模型。边界按标的/周期/attempt隔离。异步浏览器事件回调异常不是React边界的全覆盖保证，仍需本人页面验证。

移除wrapper内已未被使用的MACD/RSI/KDJ/均量/量比等重复计算，图表仍通过原适配器调用原股票方法；没有改公式、股/手单位、复权或缺失策略，也没有宣称实测网页延迟改善。现有真实除权参考/小数成交量/原价周月保护断言继续通过。

实际前端命令（目录frontend）：

```text
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-chart-boundary.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-chart-boundary.test.mjs src/components/market/stock-kline-reference.test.mjs src/components/market/tick/adapter.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test <Get-ChildItem src -Recurse -Filter '*.test.mjs'得到的全部路径>
node node_modules/typescript/bin/tsc --noEmit
pnpm build
```

初红2项缺边界模块，`logs/stock-ux-round2-boundary-red.log`；边界落地后恢复用例先红，原组件仍返回“正在加载”，`stock-ux-round2-retry-red.log`。初次源码patch因旧文本不匹配被原子拒绝，重新读取实际行后精确实施，未覆盖未知修改。随后10目标通过，`stock-ux-round2-first.log`。完整前端**696通过、0失败、0跳过**，`stock-ux-round2-frontend-all.log`；TSC **exit0**，`stock-ux-round2-tsc.log`；独立构建`.next-stock-ux-round2` **exit0**，`stock-ux-round2-build.log`。测试包含错误文案脱敏/无自动重试/手动重试动作及新加载器/原行情和配置保留；VM钩子和离线SVG不等于真实浏览器chunk失败与恢复验收。

保护：修改前1141清单中，仅声明2个股票文件变化，其他1139 SHA不变；新增2文件，新清单1143为`logs/stock-ux-round2-final-inventory.json`，原guard通过。证据`stock-ux-round2-backups.json`、`stock-ux-round2-changes.diff`、`stock-ux-round2-preservation.json`、`stock-ux-round2-final-guard.log`。Next构建新增tsconfig include已精确还原为本轮字节备份和原SHA。没有借股票修复修改期货。

原五端口仍未监听，未重启服务、恢复paid AI或应用015–018。费用为本地测试/构建，数据和模型新增付费调用0。未执行原PG/HTTP/真实行情/本人浏览器、迁移及发布回退验收；后端未改，本轮没有重跑后端用例，不引用上一轮11通过冒充本轮结果。

回退只精确恢复这两个股票文件、移除新增局部边界引用，原图表适配/行情和历史结果保留，无数据回退。人工路径：进入个股→模拟静态图表分块加载/渲染失败→仅图表区域显示可重试→手动重试后仍为原股票/周期/指标和小数成交量→切换标的不保留旧错误。本人在线路径尚未执行，不能标整包已完成并验证。

下一四包：原页面视觉/交互验收；其它A股页面流程统一；自定义因子精确版本接IC/回测/报告；真实固定数据及基准/可成交回测/复现，按全功能队列继续本地LightGBM与原功能回归。每次定时先核对最新实际进度及SHA，少重复审查，状态未变化保持安静；全部核心/金融权限/发布回退/本人验收通过前不报全系统完成。
