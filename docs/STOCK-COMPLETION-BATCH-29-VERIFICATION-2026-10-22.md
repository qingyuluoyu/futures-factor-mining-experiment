# B29 期货瞬时提醒展示隔离检查点

## 完成状态及范围

独立共享兼容包四工作包已实现并完成本机验证、私有真实PG/HTTP和本人双市场切换读取，最终预览已更新。整站仍部分完成。本轮明确改动4个共享前端展示/消费文件及新增纯策略文件，不能说“期货相关代码一字未动”；期货交易/资金/任务/金融计算及所有后端生产源码、认证/WS/布局/运行配置均未修改。

B28股票页AU2612大单截图不是新接口或用户持仓证据。已核查来源：原期货orderbook生成big_order_tick；现有期货AI任务成交生成task_order_tick。布局全市场保留行情连接，原消费和两组件无市场门禁，偏好identity未因市场切换变更，是此次跨页展示的直接原因。

## 文件与实现

- 新`frontend/src/lib/futures-alert-policy.ts`：纯展示策略，要求真实状态loaded===true、非hydrating、有accessToken、user.market_type=futures且非marketSwitchPending；不解析/记录token，不代替后端权限。
- `frontend/src/stores/market.ts`：新增策略/auth导入；在fireBigOrderAlert和scanTradesForBigOrder进入限频/成交指纹前拒绝非期货展示；task_order_tick保留原标记/记录刷新，之后才检查展示门禁，避免消耗任务提醒指纹。未改缓存、实际订单、阈值/颜色、限频/去重公式或原期货音频独立开关。
- `frontend/src/stores/notification-preferences.ts`：市场或切换pending变化时只清大单、任务两瞬时队列。非用户/会话变化不重置总偏好、通用消息、未读或保存generation，正在保存的偏好仍正常落回服务端权威值。
- `frontend/src/components/notifications/big-order-toast.tsx`、`task-alert-toast.tsx`：自身也订阅展示门禁，防存量队列在stock/pending/恢复中渲染；期货稳定态仍使用原卡片和十秒计时。
- 新`frontend/src/stores/futures-alert-isolation.test.mjs`；原`frontend/src/stores/notification-preferences.test.mjs`新增市场/生命周期边界并补明确期货稳定fixture。原断言保持。
- 新`scripts/check-stock-b29-preservation.py`、B29子计划、本记录、主计划及冻结计划最新指针；无依赖/DDL/生产迁移/Git提交或分支。

## 保护与兼容证据

开工时B28原1047保护检查通过。修改前复制4文件原字节，`logs/stock-b29-shared-backups.json`记录4个`logs/stock-b29-before-*.bin`及SHA256，最终副本完整性通过。

`logs/stock-b29-shared-diff.json`核验：从原备份仅执行11个声明hunk（导入/门禁/队列清理）前向替换即可得到当前4源文件，比较仅规范化换行；不是宣称这4文件逐字节未改。最终源SHA仍与此审计一致，`logs/stock-b29-shared-diff-final.log`。

新冻结 **1048文件最终零增删改**，`logs/stock-b29-preservation-final.log`。并且未覆盖旧B28基线，按其真实库存重算：恰好5个已声明既有文件改变（4共享+1测试）、3个新增（policy/新测试/B29保护脚本），无其他漂移，`logs/stock-b29-vs-b28-declared-diff.json`。没有把扩大白名单当成所有期货源码未动的证明。

## 实际测试与失败

- 原通知基线9通过，`logs/stock-b29-notification-baseline.log`。初次新harness存在真实React hook误用、初始化已清夹具队列的问题；只修测试适配并在初始化后明确seed非空队列，未以错误测试当业务复现。精确修正前的apply_patch一次未匹配而失败，无内容写入。修正后19测试中 **6项真实业务反例先红**，`logs/stock-b29-red-corrected.log`，实现后19绿，`stock-b29-green.log`。
- 补缓存保留、没有token、偏好写不被市场切换取消等回归。独立只读审查复现P2：缓存期货user/token在/me未完成时也会触发声音并消费指纹；新增loaded/hydrating两分支反例先红，`logs/stock-b29-review-red.log`，门禁修正后目标 **22通过**，`stock-b29-reviewed-green.log`。独立复跑22/22并确认P2闭合，本范围未见未解决明确问题。
- 最终全前端 **624通过、0失败/跳过**，`logs/stock-b29-node-final.log`。原WS分发/成交扫描/真实Zustand瞬时队列和组件源码在测试中执行，网络/声音为外围替身；这些是离线事件夹具，不冒充真实订单或运行WS已收到指定事件。
- 期货关键资金/撮合/时段日历/AI模式与规则/执行安全/回测/GP能力 **194通过、272既有警告**，`logs/stock-b29-futures-regression.log`。后端原通知/大单检测/任务提醒/pubsub/既有迁移 **50通过、1既有未await mock警告**，`logs/stock-b29-notification-backend.log`。不是本轮全量后端或全部期货功能认证。
- 原私有PG工具实际 **7组通过**，`logs/stock-b29-private-pg.log`：迁移前拒绝、兼容迁移/重入/只读检查、新旧行默认、严格bool、本人持久读取及其他用户隔离、通知生成/提示音保持、并发首次读唯一行。随机私有schema已清理，工具public_writes=0，不对public新增迁移。
- TSC exit0，`logs/stock-b29-tsc-final.log`；最终独立production构建exit0，`logs/stock-b29-build-reviewed.log`。第一次b29-final产物早于独立复查修复，弃用且未部署，不当作最终证据；部署的是`.next-stock-b29-reviewed`。两次Next自动include均用apply_patch移除后由原writeConfigurationDefaults恢复原v3和Windows尾换行，tsconfig原SHA `05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250`一致。
- 运行HTTP **9检查通过**，`logs/stock-b29-runtime-final.log`；原main/研究/EOD/归档worker正常，原GP worker false不变，release_verified=false。原1项AI任务仍paused/user，上次普通对话仍supplier rate_limited，不新增付费调用或假称对话可用。

## 实际主要命令

```powershell
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/verify_notification_popup_pg.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_notification_popup.py backend/tests/test_notification_popup_migration.py backend/tests/test_notify.py backend/tests/test_big_order.py backend/tests/test_big_order_detect.py backend/tests/test_task_alerts.py backend/tests/test_ws_notify_pubsub.py -q -rs
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_paper_custom_funds.py backend/tests/test_paper_matching_funds.py backend/tests/test_trading_mode.py backend/tests/test_trading_hours.py backend/tests/test_trading_calendar.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_ai_trading_multitf.py backend/tests/test_ai_trading_rules_edit.py backend/tests/test_ai_chat_execution_safety.py backend/tests/test_backtest_account.py backend/tests/test_backtest_runner.py backend/tests/test_backtest_signals.py backend/tests/test_backtest_segments.py backend/tests/test_backtest_synthetic.py backend/tests/test_gp_capability_guard.py -q -rs
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b29-preservation.py verify logs/futures-preservation-b29-start.json
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-ux-runtime.py
```

frontend目录执行：`$files=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName); node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files`；`node node_modules/typescript/bin/tsc --noEmit --incremental false`；`node node_modules/next/dist/bin/next build`。

共享差异审计实际通过PowerShell here-string送入Python stdin：读取副本SHA、11项精确替换与当前源比较，产出shared-diff.json；最终再次验证4当前源/4副本SHA一致。旧B28库存使用其原guard.inventory/differences核对，仅已声明delta。未创建/覆盖旧baseline。

## 人工读取、费用及数据口径

本人已有会话：新版本刷新股票历史页正常，进入股票行情，110板块及旧真实盘后状态可读；切期货后原螺纹钢主合约、分时/指标入口及MA数值可见，没有提交聊天、任务、订单或资金；再切回股票并恢复market预览，`logs/stock-b29-market-preview.png`。切换期间没有用假WS/假交易刺激运行浏览器，故不能凭截图无浮层认证真实事件完整链路，事件行为由上述源码测试与独立复查验证。

浏览器两次正常市场切换会保存本人市场偏好，最终恢复stock；因此**不声称整轮public写0**。具体DB更新次数未计量。私有PG/HTTP探针各自public写0，未调用实际订单/资金/研究写端点；没有新增行情补采或直接调用付费模型。原页面/后台行情、新闻和语音功能自身间接外部调用/费用未独立计量，不将其虚报为零。

金融公式、历史来源、单位、交易日历、税费、复权和数据版本未改变。仍显示2026-10-21盘后5548/5558、10缺/20未知；22日凌晨没有抓取或伪造当日收盘。市场切换丢弃的是已无上下文的瞬时展示，不删除服务端消息/成交历史、不修改真实任务执行或止盈止损。

## 发布、回退与限制

只核对PID文件+3001监听+Next启动命令属于本对话后，将24920切到15388，进程env WENYUAN_BUILD_DIR=.next-stock-b29-reviewed、WENYUAN_PREVIEW_BUILD=1、NEXT_PUBLIC_CHAT_SAME_ORIGIN=1、BACKEND_URL=http://127.0.0.1:8002。8002仍21688，未停止其他服务或恢复暂停任务，运行配置文件未改。

无生产DDL/数据迁移。回退先核对当前4共享源与本记录SHA，避免覆盖后续修改，按11个hunk逆向恢复或用原字节副本生成精确patch，再切自有3001到`.next-stock-b28-reviewed`；保留新文件/数据，无需删表。回退会恢复旧跨市场瞬时提醒问题，不代表修复数据；没有执行正式发布/回退演练。

只修两类期货瞬时展示，不冒充系统所有通知/音频的市场隔离。通用notification无市场Schema，仍保持原全局系统/风险/资金/交易消息、未读和声音；主播broadcast、定时报价语音、服务端会话隔离未在本包改动，需独立版本化评审与双市场回归。任务标记刷新保留原语义，当前股票上下文仍可能驱动旧期货只读记录刷新，本包不把其认证为股票Agent能力。

人工复验：本人登录3001 → 期货市场观察原提醒设置 → 切A股，两瞬时浮层立即清；不要为了验证创建订单或收费任务。需要真实事件验收时由用户选择正常事件窗口，仍不注入虚假真实记录。

## 下一四包

1. 股票报告收藏：新增版本迁移、本人/市场/JWT盘资源权限、期望状态幂等、分页与UI接通/私有PG/回退，不能只放localStorage入口。
2. 历史可信输入：已有原价/锚点/PIT与缺口只读准备，先核实授权和补采费用预算；不混qfq/raw、不改旧有效数据，不补0或因子1。
3. 股票只读Agent：独立工具Schema/引用/总预算/持久幂等和固定评测，外部429未解决不反复试模型；现有未接通入口不开放。
4. 消息/音频后续共享独立设计与正式发布验收：明确通用消息market/mode/legacy_unknown和主播/语音边界后另包兼容迁移；补全核心端到端/容量/安全/回退和人工验收。未达完成定义前保留所有续做自动化，不宣称全系统完成。
