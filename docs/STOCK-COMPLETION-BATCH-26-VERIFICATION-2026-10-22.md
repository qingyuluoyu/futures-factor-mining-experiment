# B26 A股浏览、加载和板块布局：真实交付

## 状态与边界

本轮四包的股票浏览切片已实现并通过本机测试/构建/本人已登录浏览器验证；不是整个A股、全部金融功能或正式发布验收完成。股票AI看盘/AI交易、收藏和消息的旧未接通标记继续保留；没有复制期货工具/保证金或绕过权限来伪装接通。

无后端生产代码/数据库/行情数据/金融公式变更，无新依赖、提交或分支。实际用户读取现有真实数据，不提交资金、订单、研究或付费对话。原AI任务保持1项paused/user。

## 实际文件与数据流

1. `frontend/src/components/market/stock-quote-detail.tsx`：K线/财务两个独立loading、controller、错误和重试revision。慢财务不再阻塞已验证图表，慢K线不阻塞财务视图；每通道重试不重读另一通道，总重新加载入口保留。错误不同于无数据，财务未取得时不显示“暂无覆盖”的假空态。图表symbolLabel固定证券代码，避免财务名称稍后返回使图表身份/视窗重置。所有价格、单位和复权验证沿用旧契约，取消后丢弃晚响应。
2. `frontend/src/components/market/stock-board-order.ts`（新）：仅排序已验证响应内的行业，不创造/补齐数据。数量、名称、样本涨跌幅正反排序；null涨幅始终最后，0保留为平盘；默认count降序及Unicode名称tie保持旧后端顺序，不修改原数组。仅可比过滤要求样本数>0且涨幅非null。
3. `frontend/src/components/market/stock-board-browser.tsx`：接入排序/方向/可比筛选和加载范围计数，关键词继续生效；成分股刷新传refreshKey而不是换组件身份。盘后同步/影子归档说明默认原生details折叠，仍能展开读取，不删除来源或缺口披露；解决实机状态卡把行业挤出首屏的问题。桌面6列、窄屏2/3列维持。
4. `frontend/src/components/dashboard/stock-dashboard.tsx`：选股详情打开时原工作台隐藏且inert，不卸载原股票列表；返回保持行业、搜索、排序、页码和组件身份。刷新只传revision，改行业才换key；没有新增全局/跨用户缓存，原路由user/mode key隔离不变。
5. 测试：`frontend/src/components/market/stock-quote-detail.test.mjs`、`frontend/src/components/market/stock-board-order.test.mjs`（新）、`frontend/src/components/market/stock-board-browser.test.mjs`、`frontend/src/components/dashboard/stock-dashboard.test.mjs`。
6. 新保护工具`scripts/check-stock-b26-preservation.py`；子计划`docs/superpowers/plans/2026-10-22-stock-completion-batch-26.md`；本记录及上轮UX验证记录的Node命令更正。没有修改旧保护baseline。

## 实际验证与失败证据

- 前端基线使用原已有别名加载器，**577通过**；末次全量**593通过、0失败/跳过**，`logs/stock-b26-client-final.log`。上轮无加载器命令产生的40失败是实际命令结果，但不代表40产品bug；已在原UX记录加更正，没有删除/放宽测试。
- 详情反例先4红后10绿；新增取消晚响应后纳入最终593。导航/纯排序先7红（新helper缺失断言+工作台丢挂载/身份）后14绿；板块控件及Unicode兼容先2红后12绿；成分刷新先1红、状态折叠先1红后全量通过。各`stock-b26-*-red.log`和green日志保留，不将环境错误当业务red。
- 正确股票接口/契约测试**40通过，39既有警告**：`logs/stock-b26-backend-affected-corrected.log`。首次命令误写不存在的`test_stock_market.py`和`test_stock_quote_contract.py`，exit4且0测试；`logs/stock-b26-backend-affected.log`保留，不报测试通过。
- 期货核心原15文件回归**194通过，272既有datetime.utcnow警告**，`logs/stock-b26-futures-regression.log`。包含边界/内存/SQLite等，不冒称本轮本人下单或真实PG资金事务验收。全量后端本轮未重跑，未沿用上轮2800冒充本轮新结果。
- TSC两次exit0，最终`logs/stock-b26-tsc-final.log`；独立production构建两次exit0，最终`logs/stock-b26-build-final.log`。未覆盖默认.next或正在运行产物，最终目录`.next-stock-b26-final`。
- 原有运行核验9项及股票匿名读2项通过，`logs/stock-b26-runtime-final.log`、`logs/stock-b26-stock-anonymous.json`。静态资源200、敏感接口401，原main和研究/EOD/归档worker运行；GP worker旧false，未开启。只读核对原AI任务paused/user，没有恢复收费任务。

实际主要命令（根目录，前端命令在frontend）：

```powershell
$tests=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName)
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @tests
node frontend/node_modules/typescript/bin/tsc --project frontend/tsconfig.json --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_kline_reference.py backend/tests/test_stock_market_api.py backend/tests/test_stock_market_board_api.py backend/tests/test_stock_market_capability.py backend/tests/test_stock_market_overview.py backend/tests/test_stock_screen.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_paper_custom_funds.py backend/tests/test_paper_matching_funds.py backend/tests/test_trading_mode.py backend/tests/test_trading_hours.py backend/tests/test_trading_calendar.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_ai_trading_multitf.py backend/tests/test_ai_trading_rules_edit.py backend/tests/test_ai_chat_execution_safety.py backend/tests/test_backtest_account.py backend/tests/test_backtest_runner.py backend/tests/test_backtest_signals.py backend/tests/test_backtest_segments.py backend/tests/test_backtest_synthetic.py backend/tests/test_gp_capability_guard.py -q -rs
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b26-preservation.py verify logs/futures-preservation-b26-start.json
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-ux-runtime.py
```

构建：设置WENYUAN_BUILD_DIR=.next-stock-b26-final、WENYUAN_PREVIEW_BUILD=1、NEXT_PUBLIC_CHAT_SAME_ORIGIN=1、BACKEND_URL=http://127.0.0.1:8002，再于frontend执行`node node_modules/next/dist/bin/next build`。参数只传本对话新进程环境，没有修改.env。

## 保护与配置恢复

开工新baseline保护1040文件，明确豁免本轮8个股票文件；旧B25及UX差异仍保留。Next构建自动追加tsconfig类型目录，移除临时include后曾因末尾CRLF/LF导致字节保护失败，仅这一个文件，`logs/stock-b26-tsconfig-format-audit.json`证实配置内容完全相同。没有掩盖/重写baseline/扩大允许名单：用原Next配置生成器恢复原include和Windows结尾，最终**1040文件全部逐字节一致，新增/删除/改动0**，`logs/stock-b26-preservation-final.log`。

实际恢复命令（frontend）：
`node -e "require('next/dist/lib/typescript/writeConfigurationDefaults').writeConfigurationDefaults(require('typescript'),'tsconfig.json',false,true,'.next-wenyuan-preview-v3',false).catch(error=>{console.error(error.name);process.exitCode=1})"`
只为恢复原生成配置，未新增formatter或放宽TS检查。首次B26预览切换命令未在hash失败后及时退出，失败证据保留；当时仅末尾换行不同，金融生产代码无变。最终切换明确先验证hash并在非0时拒绝，最终保护通过后才切入final产物。

## 本人浏览器与数据口径

- 沿用用户已登录页面，不读取凭据；工作台输入“平安”，真实返回3只→打开000001.SZ→查看前复权K线和估值/财务→返回，输入“平安”和3条结果保留；随后清空测试搜索。
- 行情读取5558目录、110行业、2026-10-21快照5548/5558、缺10/未匹配20。真实选择样本涨幅排序，首项渔业+3.02%、林业+2.41%、出版+2.25%；这只是已有当日样本平均，不是官方指数或独立数据认证。
- 1280宽截图显示首行六卡和新排序工具，`logs/stock-b26-market-preview.png`；472窄视口也实际查看。折叠状态可展开，显示未到北京时间16:30、最近已存21日，未抓22日未收盘行情；最终恢复默认折叠，不抹掉数据异常。
- 个股图表仍为原前复权历史至16日，盘后原价快照至21日，分别披露，没有为“新数据更多”把不兼容锚点/复权拼接。未新增数据、填0、复活过期价格或修改指标计算。

## 兼容、成本、回退与人工路径

无接口破坏、DDL、数据迁移或金融方法变化。新增模型/上游请求0；本次用户操作仅站内已存行情/财务读取。既有后台调度继续运行，其原新闻/数据费用不能据此称为0。没有新真实资金接入或模拟账本副作用。

3001仅停止逐次核实CommandLine的本对话Next进程并用既有隐藏spawn工具起final目录；8002、3000及其他服务未停止。回退可只切回旧`.next-wenyuan-preview-v3`或先前B26产物，不能抢占他人端口；代码按本轮8文件/新增helper逐项恢复，数据库不删除任何用户记录。现无Git提交，不虚构一键revert。

人工复验：http://127.0.0.1:3001/market → 板块排序/方向/仅可比 → 行业→股票；工作台搜索“平安”→详情→返回，查询应保留；盘后状态可展开。不要为本轮验收进行资金、下单或模型测试。

下一三包：①独立股票只读Agent及可追溯引用/持久幂等/总预算，不能借期货工具解除旧409，供应商429仍待配额核验；②历史原价/复权锚点/PIT和五年研究资源/可信回放扩池，先有来源/费用预算不盲补；③股票研究配置收藏/消息及已完成报告工作流，按本人/市场/JWT盘独立持久化，不假称未接通菜单可用。正式发布全链路、容量与数据库回退人工验收仍未完成。
