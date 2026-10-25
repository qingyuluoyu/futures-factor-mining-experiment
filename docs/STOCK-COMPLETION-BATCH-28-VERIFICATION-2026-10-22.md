# B28 股票历史研究检索检查点

## 完成边界

本轮四包已实现、运行本机回归、真实隔离PG/原ASGI和本人已有会话的读取验证，并部署到3001；全系统仍部分完成。不是股票AI、收藏/消息、五年/PIT或正式发布认证。期货/后端生产和共享认证/网关/布局/配置未修改，1047保护文件最终原字节一致。保留续做自动化。

## 实际文件与数据流

- `frontend/src/components/factor-research/stock-research-history.tsx`：新增六状态与研究类型组合筛选，使用原后端status参数，无新查询协议。条件改变回第一页；列表的loadedQuery与loading/error联动，不让旧页按钮或旧总数冒充当前筛选。空页、加载、失败明确区分。
- 同文件：原offset/kind effect在翻页时会取消详情却不再发详情请求，导致详情一直加载。改为页面身份生命周期与列表生命周期分开，翻页/筛选不取消已读详情或清空对照，卸载/换登录身份仍取消。原详情/导出/配置回填入口保留，不自动创建研究。
- `frontend/src/lib/stock-research-api.ts`：详情/取消响应必须与请求ID一致，错误取消响应保留“刷新核对、未自动重试”的不确定性。列表必须符合请求limit、唯一任务、kind/status、total和has_more；非终页必须满limit，否则固定offset步长会漏过未展示记录。已越过缩小total的空终页仍合法，不伪造条目或强迫回到第一页。
- `frontend/src/lib/stock-research-api.test.mjs`、`frontend/src/components/factor-research/stock-research-history.test.mjs`：行为与契约反例、独立请求、卸载、条件切换、对照保留、合法旧页等。
- 新`scripts/verify-stock-research-history-local.py`：仅loopback PG5433，随机私有schema复制既有表结构，276个本人/virtual及两组各12个不同用户/盘的明确QA任务夹具。原ASGI、原迁移readiness及原服务筛选；数据库读请求设READ ONLY，夹具不是实际金融研究，不调用研究worker或计算收益。所有私有表/schema已清理；公开AI任务及研究任务摘要在探针前后相同。
- 新`scripts/check-stock-b28-preservation.py`、B28子计划、本记录和主计划最新指针。只有4个既有股票源/测试文件改变；没有后端生产改动、数据库版本迁移、新依赖、Git提交或分支。

## 验证与失败证据

- 初始目标30测试：25通过、5项按预期先红（无状态入口、翻页误取消详情、残留旧页、错ID、矛盾分页），`logs/stock-b28-red.log`。实现后30绿。复查空非终页先1红后修，`stock-b28-review-red.log`。
- 独立只读审查复现P2：非空但不足limit的非终页会让UI按固定20跳过未展示记录；新增具体反例先红，`logs/stock-b28-independent-review-red.log`，随后修复。最终目标33通过，`logs/stock-b28-reviewed-green.log`，独立复跑33/33并确认该项关闭，未发现剩余明确阻断。不是通过放宽断言解决。
- 最终全前端 **611通过、0失败/跳过**，`logs/stock-b28-node-final.log`。TSC exit0，`logs/stock-b28-tsc-final.log`。完整独立production build exit0，`logs/stock-b28-build-reviewed.log`。
- 后端受影响研究API/服务/报告及既有迁移 **65通过、160既有警告**，`logs/stock-b28-backend-affected.log`；期货资金/撮合/盘模式/AI隔离和执行安全 **81通过、272既有警告**，`logs/stock-b28-futures-safety.log`。没有运行本轮全量后端，不以此认证所有期货功能。
- 真实私有PG/ASGI **35检查通过**，`logs/stock-b28-real-pg.log`，原响应 `logs/stock-b28-real-history.json`。覆盖六状态各两页、真实Schema约束、原数据库readiness、相同owner不同JWT盘/其他用户404及独立列表、非法状态/过大页422、期货上下文409、公开摘要不变及清理。验证认证使用本地fixture上下文，不是生产JWT权限认证。
- 其中12个真实列表和1个详情共 **13份响应**，通过最终股票客户端解析，`logs/stock-b28-real-client.log`。这是对真实私有PG原响应的离线fetch适配解析，不将其冒充浏览器网络或真实金融研究成果。
- 本人原已登录会话：历史页显示本人1份旧已完成组合回测；失败筛选真实返回0、与组合回测类型合并仍为0；切已完成恢复1项；打开原报告详情、方法/数据版本/导出按钮可读，进入恢复配置只加载原参数，无创建/取消/交易/资金写入或模型调用。最后恢复默认历史列表。`logs/stock-b28-history-clean-preview.png`。
- 运行HTTP9检查通过，`logs/stock-b28-runtime-final.log`；8002仍原main生命周期及研究/EOD/归档worker，GP worker原false未改；原1项AI任务仍paused/user。上次普通AI失败仍provider_rate_limited，未复测、未说对话可用。
- 最终保护 **1047文件、无增删改**，`logs/stock-b28-preservation-final.log`。Next两次构建生成的tsconfig临时include均用apply_patch移除，再用原writeConfigurationDefaults恢复v3 include及原Windows尾换行；hash完全恢复，不放宽保护白名单。
- 初始`b28-final`构建和一次短暂预览早于独立P2修复，未作为最终构建交付；后续最终运行的是复查修复后`b28-reviewed`。探索中少数猜测路径不存在，只读定位已更正；一次计划勾选apply_patch未匹配整行而失败，未写入内容，随后精确整行更新成功，不冒充验证命令通过。

## 主要实际命令

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-stock-research-history-local.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_jobs_api.py backend/tests/test_stock_research_job_service.py backend/tests/test_stock_report_artifacts.py backend/tests/test_stock_research_jobs_migration.py -q -rs
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_paper_custom_funds.py backend/tests/test_paper_matching_funds.py backend/tests/test_trading_mode.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_ai_chat_execution_safety.py -q -rs
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b28-preservation.py verify logs/futures-preservation-b28-start.json
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-ux-runtime.py
```

frontend目录：`$files=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName); node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files`；`node node_modules/typescript/bin/tsc --noEmit --incremental false`；`node node_modules/next/dist/bin/next build`。

真实客户端解析以同一Node loader导入stock-research-api，逐一遍历`logs/stock-b28-real-history.json`的responses，通过listStockResearchJobs/getStockResearchJob读取离线返回，不发网络。

最终build/start进程环境：WENYUAN_BUILD_DIR=.next-stock-b28-reviewed、WENYUAN_PREVIEW_BUILD=1、NEXT_PUBLIC_CHAT_SAME_ORIGIN=1、BACKEND_URL=http://127.0.0.1:8002。只核对本对话PID文件、3001监听和Next命令后两次切换自有预览进程，最后24920；backend21688、3000及其他服务未停止，运行配置文件未变。

## 金融口径、兼容、费用与回退

金融公式/手续费/交易日历/价格/因子/复权及数据版本语义均未改。仅加强消费一致性；历史任务原方法与数据版本继续显示。读旧报告或恢复配置不代表同版本可复算、PIT认证或可信实盘策略。

没有public业务写、数据库迁移或模型/行情源新增调用；既有后台行情/新闻调度仍运行，其自身费用未计量。私有夹具只检验任务检索/权限，不充当真实报告或价量。无日/月次数限制恢复；原并发、参数与幂等保持。

并发状态变化若让原count/list两次查询产生矛盾，新客户端会显错，需刷新后核对，不以空页隐藏；本轮不改变后端事务隔离。分页仍受10000 offset原上限，不声称无界遍历已实现。

回退：只在确认所有权后的3001进程切回`.next-stock-b27-final`及原env，或恢复本轮4个股票文件；不改表、回填数据、清理缓存或恢复暂停任务。无Git，不虚构git revert或正式发布回滚演练。

人工路径：http://127.0.0.1:3001/history → 任务状态/研究类型 → 本人已完成报告 → 详情/恢复配置。真实页面只有1项，跨多页/延迟失败/错响应来自自动反例和私有真实SQL夹具；未伪称本人页面人工点过多页。

## 剩余与下一四包

1. **共享通知只读调查/独立兼容包：** 本人A股历史页期间出现过AU2612期货大单提醒浮层，稍后自行消失，证据`logs/stock-b28-history-preview.png`；不是本轮新增代码。先厘清是否全市场订阅与市场展示语义冲突，再单独设计兼容、双市场回归和回退，不在股票白名单改共享通知。用户无需为本轮验收修改设置。
2. **股票报告收藏垂直切片：** 独立版本迁移、用户/市场/JWT盘资源权限、期望状态写幂等、分页/真实PG/UI/恢复；当前只做了状态检索，不冒充收藏已接通。
3. **历史可信输入：** 明确来源授权/费用预算后，处理原价/复权锚点/财报可得时间PIT和缺口；只读准备先行，不拼接21日raw与16日qfq、不造价量、不无预算补采。2026-10-22凌晨未收盘，不抓取当日收盘。
4. **股票只读Agent与发布：** 独立工具权限、引用、总预算/持久幂等/固定评测，供应商429未解决不无预算试模型；保留未接通入口。正式容量、安全、发布回退和全站人工双市场验收未完成，不能报告完整系统可用。
