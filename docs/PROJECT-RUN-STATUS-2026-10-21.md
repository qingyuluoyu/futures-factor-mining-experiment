# 完整运行请求：启动检查点

最新状态：**用户已明确选择保持暂停；正常业务后端与原研究/EOD/归档worker已启动，不是预览模式。** 真实维护/验证/费用/边界见 `docs/PROJECT-FULL-RUN-VERIFICATION-2026-10-21.md`、`docs/superpowers/plans/2026-10-21-full-local-startup.md`。整体整改/生产上线未完成。以下保留此前等待用户决定时的历史检查点，不代表当前仍阻塞。

## 三个运行工作包

1. 已完成只读核对：B24保护1014文件零漂移；开工3000/8002/6380/5433/8110均无监听。原main生命周期不运行遗留DDL，但会启动原scheduler、AI自动恢复、研究/EOD/归档等。配置股票回放/研究/EOD/自动EOD/归档开启，GP新任务关闭、catchup关闭。真实PG READ ONLY聚合：既有AI交易1条paused且pause_reason=market_closed；主播/超级因子无任务，研究仅已终态任务。未打印密钥或改用户任务状态。
2. 已完成安全依赖启动：仅对原专用本地PG目录F:/qihuo-pg-data确认无服务后启动5433；复用原local_service.spawn隐藏启动WSL keepalive/Redis6380、原Sina bridge8110及Next前端3000。Frontend进程BACKEND_URL指8002，没有修改env/共享配置或生产代码，没有停止其他进程。此时没有业务API，登录业务尚不可用。
3. 未完成：完整backend8002、研究/EOD/归档worker和本人页面正常API验证。阻塞是原AI任务在开盘自动恢复并持续付费；已有单次模型上限不能推断用户批准持续消费。已向用户异步询问“保持任务暂停”或“允许恢复并另外给预算”。未自行修改其pause_reason、provider、余额或状态，也没有启动后台再暗中停用/替换原AI循环。得到明确决定后再完成正常生命周期启动与验证，不能将预览入口换名字充当完整项目。

## 实际命令与结果

- 原 `backend/scripts/local_service.py spawn` 用于logs/full-run-postgres.spawn.json、full-wsl-20261021.spawn.json、full-bridge-20261021.spawn.json、full-frontend-20261021.spawn.json；PID各存同名前缀.pid，退出前必须核对端口/命令行，不能按陈旧PID杀进程。
- `backend/.venv/Scripts/python.exe -X utf8 backend/scripts/local_service.py redis-check` 退出0。
- `backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b24-final.json` 1014文件零漂移。
- `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_local_startup_script.py backend/tests/test_database_startup_readiness.py -q`：11通过，日志logs/full-run-startup-tests-20261021.log。
- 实际HTTP：3000/login=200，8110/health=200且ok/redis=true。证据logs/full-run-20261021-status.json，明确backend_started=false。没有认证本人业务、模型调用或当日A股同步测试；不重复前轮付费测试。

无代码/迁移/金融公式变更。启动原期货桥可能按原配置刷新其行情缓存，不是来源独立认证。未启用股票采集或模型，未创建测试用户/资金/委托。保留所有业务数据；回退只停止核实为本轮新进程的服务，不删除数据库、缓存或用户账户。

下一步：用户决定自动AI策略→安全启动原backend并验证实际worker→验证前端代理/登录/研究及盘后状态。若选择暂停，应先制定经过本人授权且不触发交易的合法操作；现有pause API只接受running任务，不能为绕过此限制先恢复任务，也不能直接伪造状态迁移。原正式WebSocket日志的query凭据脱敏仍须在独立运行包装处理，不直接改冻结认证/期货文件。
