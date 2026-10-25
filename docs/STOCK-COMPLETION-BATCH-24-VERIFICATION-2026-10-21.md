# B24 安全预览与本人 AI 实际验收

## 状态与修改边界

本轮四包完成手动预览入口、安全边界修正、本人页面及一次普通AI对话验收、检查点。**全系统仍部分完成**，股票聊天/AI交易等未开放，正式worker/迁移发布/生产构建及双市场全链路没有验收。不能将手动预览称为完整正式运行。

新增代码仅 `backend/scripts/preview_service.py`、`backend/tests/test_manual_preview_service.py`；新增本记录与 `docs/superpowers/plans/2026-10-21-stock-completion-batch-24.md`，更新主计划和期货冻结路线的续做指针。已有期货、共享认证/网关/布局/运行配置未修改，无数据库结构或金融计算变更，无新依赖、提交或分支。

更正B23记录：当前 `app/main.py` 的实际 lifespan 已使用 `verify_database_schema`，没有调用遗留 `_init_tables`；“启动仍会执行遗留DDL”的断言不成立。此次不为这条过时判断修改main。但原正常生命周期确实启动scheduler、存量AI/GP/研究/EOD等，不能盲目启动并视为免费无副作用。

## 实现与数据流

独立入口调用原create_app，替换**这个预览进程**的lifespan。连接专用本地PG5433/Redis6380，只读检查原39表496列；只证明表列存在，不证明金融/迁移全正确。EOD和影子归档执行既有迁移 `check=True` 的结构/版本检查，不执行DDL或数据修补；保留已存盘后读取，sync/catchup关闭、所有worker为空。前端仅进程内BACKEND_URL指向8002，不改任何env文件。

HTTP读写分别精确白名单；被暂停的任务、外源新闻和期货K线兜底入口503，不把GET当无副作用。允许入口仍经过原身份/市场/资源/盘校验，不替代权限依赖。匿名真实HTTP验证401。原回放本人操作入口保留，但本轮不通过UI进行资金、委托、日期推进等变更。预览状态 `/preview/status` 明确manual-no-workers、release_verified=false、股票chat不可用、原聊天仍有RAG/memory/tools、子调用费用未测。期货实时行情、自动任务及正式全部模块不是本预览认证范围，health200/缓存key存在不能证明行情新鲜。

uvicorn的WebSocket握手日志即使access_log关闭仍可能包含query token，已在新增入口给uvicorn.error/access安装过滤器，覆盖token/access_token/refresh_token/api_key/key。历史本轮预览日志离线脱敏，不重复打印值；不改冻结的原WS或认证文件。审查者额外指出public WebSocket使用key参数，已补对应断言。`logs/b24-log-redaction-audit.json`审计通过；原正式入口相同日志风险须另包兼容修复，不能据此声称生产日志全部安全。

## 本人浏览器验收

用户在页面自行登录已有账号，未索要密码、读取认证存储或伪造JWT。

- 期货工作台可显示本人既有缓存/任务摘要。打开原助手，已有DeepSeek Flash (SenseNova)模型，一次UI短问题“请只回复四个字：连接成功。不要查询行情、不要调用工具、不要进行任何交易。”，实际回答 **“连接成功。”**。截图 `logs/b24-ai-preview.png`；不是mock/模型列表/200替代回答。未再测试供应商、索价或交易。
- 这是**一次UI提交**，不是已验证的一次上游请求。原代码无条件RAG可调用embedding，持久会话还可能触发记忆嵌入，原网关工具最多五轮；实际子调用数/token/金额未计量。不能宣布预算闭环、所有模型、行情分析工具、聊天交易或A股聊天可用。A股全局助手隐藏、后端409未开放，没有删除保护或借期货工具代替。
- A股工作台/行情、行业→银行42成分→平安银行详情入口可读。修复预览读取位后实际展示 **5558目录 / 110行业 / 5549覆盖，2026-10-20，9缺价 / 19目录未匹配**，5568来源行；来自已存Teajoin盘后快照，不是此次补采，也不是21日未收盘价。先前预览漏初始化读取位退回299/5558、16日legacy，已修正，不改原股票生产读路径。
- 1440×1000响应断点实际DOM首六卡同一y、约195px宽，第七卡下一行；证据 `logs/b24-market-dom-proof.json`。是本次桌面六列布局验收，不保证所有设备已测。结束恢复默认视口。
- 个股历史1500根前复权至16日，与20日不复权盘后快照分开披露，没有拼接基准。周K合成317根、MACD和BOLL切换实际生效，截图 `logs/b24-stock-chart-preview.png`。测试后恢复原日K/MACD关/BOLL关，避免将测试设置留到模拟工作区。分时、分钟、异动未接入，按钮明确禁用。
- 因子研究页面读取实际覆盖：研究历史300只有价证券、585392日线，最新16日；选区61观测日有34个价格缺格。目录5558不等于完整研究池。执行器因本预览无worker明确未就绪、开始按钮禁用，未提交或伪装研究成功。
- 本人历史报告列表显示既有一份完成组合回测，可读类型/状态/分页；没有新建、重算、对照两份报告或认证旧报告收益。模拟盘读取本人既有账户、固定快照及订单页，新“全部已存委托历史”显式查询实际返回匹配0笔、revision3、游标2026-09-04，分页禁用且没有伪造记录；未创建资金/订单、撮合、撤单或推进日期。
- 未逐个执行全部菜单/异常路径，股票AI看盘、AI交易、收藏、消息仍标记未接通。真实用户成交等写操作验收未做；前轮私有PG撮合证据不能冒充本次本人交易验收。

## 实际验证、失败与命令

工作目录均项目根，前端命令在frontend运行；日志不存凭据。

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_manual_preview_service.py backend/tests/test_database_startup_readiness.py backend/tests/test_ai_chat_execution_safety.py backend/tests/test_stock_replay_history.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_manual_preview_service.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/local_service.py redis-check
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/local_service.py spawn logs/b24-preview-wsl.spawn.json
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/local_service.py spawn logs/b24-preview-backend.spawn.json
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/local_service.py spawn logs/b24-preview-frontend.spawn.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b23-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py snapshot logs/futures-preservation-b24-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b24-final.json
```

```powershell
$files=@(Get-ChildItem -Path src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName)
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files
node node_modules/typescript/bin/tsc --noEmit
```

- 初始预览测试3红，新模块缺失；补生命周期关闭失败/切市场路径2红，修复嵌套finally与精确路径。后续只读审查发现GET外源、EOD读取位、批量模型路径和日志key遗漏，逐项修复。
- 原路由匿名测试首轮缺session_factory；补测试会话时发现未安装aiosqlite，**没有安装新依赖**，改用既有真实SQLite适配器，补其测试局部rollback/commit后通过。没有修改/跳过原测试或放宽401/503断言。
- `logs/b24-backend-target-fixed.log`：71通过、6既有警告；补最后key过滤后 `b24-preview-final.log` 新预览10通过。最终全后端 `b24-backend-release-check.log`：**2741通过、3既有skip、741warnings，154.74秒**；此前完整运行2738/2741日志保留，不用早期结果替代最终结果。
- `logs/b24-node-full.log`：**532通过、0skip**；`logs/b24-tsc.log`退出0。前端生产代码没有本轮修改。
- 实际PG只读readiness39表496列；真实运行前后各项HTTP探针分别12项和7项通过，`logs/b24-real-http.json`、`logs/b24-real-http-reviewed.json`。探针不带凭据、不调用模型/源、不写资金；本人浏览器另外验证，不能将匿名401代替本人正常路径。
- Redis曾因WSL短命退出/中继刚启动而超时，未无限重试；用既有隐藏spawn保持专用WSL，再ping成功。PG一直运行，不启停PG或他人3001。后端只在核对命令行属于本轮preview_service后重启加载修复，未停止原正式后台任务，因为原8002开工无监听。
- 原B23-final保护开工1013通过；最终原清单差异**仅新增backend/scripts/preview_service.py**，已有受保护文件未改/删除。扩展B24-final **1014文件零漂移**，不重写旧基线或称旧verify最终通过。
- 本轮无独立私有PG写集成脚本、无DDL/业务计算变更；真实PG用于只读迁移校验/HTTP/本人读取。生产build/standalone发布、正式workers、迁移回退演练、完整两市场写链路和AI预算评测**未执行**。审查者最后key脱敏复核因会话额度受限未完成；此前建议已实现且本地10测通过，不冒称独立最终签字。

## 运行、回退、费用与下一四包

当前手动预览前端 `http://127.0.0.1:3000`、后端8002、专用Redis6380、PG5433。启动规格/PID在 `logs/b24-preview-*.spawn.json` / `.pid`，PID会变化，停止前必须核实进程命令行与端口；不能仅凭陈旧PID停止其他服务。原run-local未改；其身份检查可能拒绝复用本预览8002，先按本预览规格核实并退出，不能强抢。

无数据库迁移或新数据补采，无新增行情源费用。用户登录、切市场偏好和原聊天持久化属于明确授权的本人交互，会保留；不能靠代码回滚抹除这些数据。回退仅停止本轮独立进程、移除新增入口，不清Redis、回写旧价、删除报告或交易数据。普通AI本次供应商费用未计量，不以短回答推断免费。手动预览自动研究/挖掘/交易/EOD暂停，不恢复次数限制，也不绕供应商TPM/RPM。

人工继续：保留预览页面；行情看目录/实际覆盖和来源日期→行业→个股切换；交易仅查看本人账户和全部历史；切到期货打开助手可复现普通文本回答，但复测会产生供应商费用。自动任务创建需要正式worker独立验收后开放，不能删除503换取假运行。

下一四包保持：
1. 股票独立只读聊天的持久幂等、身份/市场/盘隔离、明确上下文及token/时间/费用预算；不能解冻或复制期货高权限工具。
2. 历史PIT/财报修订/复权锚点/小数成交量、五年缺价格的安全版本与来源核查；9缺/19unknown需证据/预算，不填零或盲导。
3. 可信股票DSL、真实基准、可复算研究和回放扩池；现有回放快照范围不冒称全市场。
4. 正式worker、独立production build/standalone、迁移兼容/前向修复/回退、期货不退化和双市场人工发布验收。所有续做保留，系统未完成。
