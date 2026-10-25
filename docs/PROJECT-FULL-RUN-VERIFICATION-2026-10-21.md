# 原项目正常生命周期启动：验证记录

## 完成边界

**正常本机服务已启动并验证，不再是B24手动预览。** 用户明确选择“保持该任务暂停，先完整打开系统”。该任务保持paused，自动恢复意图由market_closed变为user，未恢复、触发模型、下单、平仓或改变资金/策略。

系统整改仍部分完成；股票聊天/AI交易等未接通能力、GP新任务未开启、金融数据/PIT/五年/基准/发布验收没有因此完成。当前是Next开发服务，不是已完成生产build/上线认证。

## 三包实际修改与数据流

1. 新 `backend/scripts/offline_user_pause.py`：本地DB所有者按本次人类明确授权，对精确task_id/user_id/trading_mode/updated_at及paused/market_closed/ai条件作CAS；不写status或任何资金、持仓、模型/策略参数。同一SQL CTE更新暂停意图并强制写原ai_trading_run_logs审计，审计失败整体回滚。固定授权引用和确定UUID审计实现重复幂等；不会先启动任务来绕过原HTTP pause仅接受running的限制。工具只离线执行，8002已在线即拒绝；不是对外权限接口，不使用伪造JWT。实际首次changed=true，再次changed=false，审计ID相同。只有pause_reason/note/updated_at变化，其余全部任务字段聚合摘要前后相同。
2. 新 `backend/scripts/full_local_backend.py`：只读检查专用PG5433/Redis6380、39表496列及不存在尚待自动恢复的付费AI任务，才运行原 `app.main:app`。没有预览lifespan、请求白名单、工具/worker替换、鉴权覆盖或依赖mock。复用原日志filter保护query凭据，本进程APP_ENV=local_runtime关闭SQL调试输出，未写env文件。新增无凭据只读 `/local-runtime/status` 暴露真实后台任务状态和release_verified=false。
3. 新 `backend/tests/test_full_local_backend.py`、`scripts/test-offline-ai-pause-postgres.py`：红绿、安全/兼容验证、真实私有PG CAS/回滚，实际启动/HTTP/浏览器和检查点。没有修改原期货、共享认证/网关/布局/模型或运行配置，无新依赖、生产DDL、提交或分支。

## 运行证据

地址 `http://127.0.0.1:3000/login`，已通过CUA看到正常登录表单并保留页面；截图 `logs/full-startup-login-20261021.png`。未索要密码/密钥、读取认证存储或提交登录，当前本人业务正常写链路未验收；前轮AI非空回答不是本轮供应商测试。

真实监听：3000前端、8002业务后端、6380Redis、5433专用PG、8110原行情桥。原3000/6380/5433/8110服务复用，不抢占/关闭他人进程；8002开工无监听后隐藏spawn。规格/PID在 `logs/full-backend-20261021.spawn.json` / `.pid`，实际PID可变，停止前必须核对端口与命令行。

`logs/full-run-final-http-20261021.json`：后端health200，正常runtime状态200，**/preview/status=404**，后端registration-status200、前端代理同接口200，行情桥health200且ok/redis=true。正常runtime实际scheduler_master=true、stock_research_worker=true、stock_eod_worker=true、stock_archive_worker=true、gp_checkpoint_worker=false（原配置未开启）。不是假造worker已运行或仅前端页面200。

正常生命周期在北京时间19:53启动并于19:54完成已授权Teajoin的**21日**盘后一次批次，attempts=1、partially_completed；只读状态 `logs/full-startup-runtime-data-20261021.json` 显示最新已存快照：**5548/5558覆盖、10缺行情、20目录未匹配，不复权，原始响应证据存在**。不是21日实时行情、完整历史/全部市场/独立真实性认证；不将缺价填零或武断归为停牌。旧20日快照保留。原归档worker启动后完成一份已有5549行影子归档、source_calls=0/model_calls=0；未据此宣称新21日归档已完成。

启动警告仍有VVTR主力同步无结果、VVTR K线未启用/跳过tickdata同步，保留现存合约数据；未解冻期货代码去修这些外源风险。不因health200证明所有期货行情新鲜或全部功能无故障。

## 实际命令与验证

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_full_local_backend.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_full_local_backend.py backend/tests/test_manual_preview_service.py backend/tests/test_local_startup_script.py backend/tests/test_database_startup_readiness.py backend/tests/test_ai_chat_execution_safety.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-offline-ai-pause-postgres.py
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/offline_user_pause.py --target logs/authorized-pause-target-20261021.json --confirm-user-request
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/local_service.py spawn logs/full-backend-20261021.spawn.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b24-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py snapshot logs/futures-preservation-full-startup-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-full-startup-final.json
```

- 初红2失败，新模块缺失（full-startup-red.log）；实现后原启动等23通过（full-startup-green.log）。真实操作首轮因configure_environment切cwd导致相对target不存在，**FileNotFoundError发生在写库前**；补路径顺序反例1红后先resolve再切目录，3绿（full-startup-path-red/green.log）。失败证据保留，没有删测试或改原认证/交易规则。
- 最终受影响67测试通过（full-startup-target-tests.log）。未运行本轮完整后端/Node/TSC或生产build；源码仅本地运行工具，不把B24的2741/532当本轮执行结果。
- 结束前再次运行同一受影响集合，full-startup-final-recheck.log仍67通过；full-startup-final-checks.json实际核验登录页/代理200、预览404、原研究GET/POST匿名401（不是预览503）、正常worker与主锁true、本人任务paused/user及其余字段摘要未变。1016保护再次零漂移。
- 真实私有PG12检查通过（full-startup-pause-pg.json、full-startup-pause-pg-final.log）：授权操作、重复/单审计、其他字段不变、owner/mode/time/running/strategy/other-reason拒绝、审计约束失败整体回滚、公表任务摘要不变，schema已清理。私有夹具没有真实交易或行情，未向public写测试账户/资金/订单。
- 精确本人的实际维护操作结果 `authorized-pause-apply-fixed-20261021.json`、`authorized-pause-idempotent-20261021.json`；授权target元数据仅本地记录，不含密钥。后端启动前预检无待自动恢复AI；启动后只读确认仍一条paused/user且任务其他字段摘要相同。新增一条强制生命周期审计，不删除旧日志。
- 保护：B24原1014无改/删，仅新增两个运行工具；原verify失败差异如实保留。扩展 `futures-preservation-full-startup-final.json` **1016文件零漂移**。不把新增文件说成原清单完全一致，也不以哈希证明运行功能全正确。

## 成本、迁移与回退

本轮维护/验证0模型调用，未额外付费对话。原盘后调度执行一次已授权数据批次，沿用最多3上游请求/单次90秒/失败不自动重试/每次仅一天及catchup关闭，不盲批重导；供应商计价金额未测，不能称数据源免费。继续运行会按原配置在后续交易日盘后采集，不恢复日/月业务次数限制。仅开启服务不是给未授权付费AI任务新预算。

没有数据库结构迁移。实际业务数据变化仅本人授权任务暂停元数据+其事务审计，以及正常原EOD/影子归档运行产生的真实批次；不回滚这些已存真数据。服务回退是核实并停止本轮full_local_backend进程，保留手动暂停状态和审计。后续若用户想恢复任务，通过原AI交易开始入口由用户主动执行，可能触发模型费用；不能恢复旧market_closed意图制造开盘自动扣费。

原run-local身份检查未更新，可能拒绝复用本轮full_local_backend进程；后续运行先读本记录和进程实际命令，不强抢8002或擅自启用旧遗留初始化。该包装只增运行检查，不替代正式发布方案。

## 人工入口及下一三包

本人在正常登录页登录→A股行情核对21日来源/覆盖缺口→因子研究刷新服务状态（已启研究worker，不保证缺价策略算得成功）→历史报告/日线模拟只在本人授权下使用。未连接真实资金账户。旧股票AI未开放入口仍保持关闭，不以取消预览503说所有功能都已补齐。

下一三包：股票独立只读AI的持久幂等/上下文/预算；21日10缺20未知与历史PIT/锚点/五年缺价证据；可信DSL/真实基准/回放扩池和正式构建发布回退及双市场人工验收。整体未完成，续做自动化保留。
