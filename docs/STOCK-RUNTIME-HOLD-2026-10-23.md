# 2026-10-23 用户主动停机检查点

**后续授权更新：** 用户在同日明确要求“现在项目完整运行一下”，授权本次恢复原项目服务。以下停机说明为历史记录；AI付费任务仍保持paused/user，新课程015/016/017不随启动应用，未完成的新v2入口不公开。启动实际命令、构建与HTTP结果另存本次运行检查点，不能将本次启动等同完整课程/生产验收。

## 当前最高优先级运行约束

用户明确回复：**“是我关闭的，暂时不要重启”**。保留该停机状态；后续自动化不得因旧B32/B33启动计划、HTTP不可达或无人监听而恢复PG/Redis/后端/前端/connector，也不得自动恢复付费AI。只有用户明确允许恢复后再核实服务归属和数据状态。此约束不禁止安全的离线代码分析、测试和文档工作；不是停止所有项目整改或删除续做自动化的授权。

## 本轮三个处置包

1. 运行现状确认：浏览器工具已恢复，但库存无原标签页；通过正式CUA打开3001行情页实际ERR_CONNECTION_REFUSED。netstat未见3001/8002/8110/5433/6380监听，B33记录的自有PID均不存在。没有用旧健康结果声称项目仍运行，没有重启/停止任何服务。
2. 安全边界与离线验证：`python -m pytest backend/tests/test_stock_preview_connector.py backend/tests/test_stock_archive_observations.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q`，23通过，`logs/stock-b34-offline-regression.log`。`python scripts/check-stock-b33-preservation.py verify logs/futures-preservation-b33-start.json`，1082文件零增删改，`logs/stock-b34-preservation.log`。Python为`backend/.venv/Scripts/python.exe -X utf8`。未修改生产代码、配置或金融口径，无DDL、数据导入/修复、源/模型调用。
3. 停机归因和恢复门禁：主动向用户核实后确认人工关闭，运行恢复包转为等待用户允许；不能把这次端口关闭认定为A股/期货代码缺陷，不反复拉起后台对抗用户。本人浏览器/真实PG/HTTP验收未执行，B32 P4及整体正式发布保持未完成。

## 真实日志及不能推出的结论

PG日志记录07:02:02进程24168执行ROLLBACK时异常0x40010004，随后其他进程终止、重初始化失败并关闭。`pg_ctl -D F:/qihuo-pg-data status`为no server running；只读`pg_controldata -D F:/qihuo-pg-data`显示cluster state in production、最近checkpoint 04:52:03。未运行pg_resetwal、删除pid/锁、恢复整库、设置ignore/recovery绕过或手工改表；该状态只提示正常恢复前需检查，不能单凭它判定数据丢失/损坏或认证数据完整。

诊断脚本确认当前执行器Python在Windows Job内，但这不足以证明Job清理导致停机。第一次ctypes诊断未声明HANDLE参数触发OverflowError，补参数签名后调用成功；没有据此修改启动flags/权限或使用计划任务逃逸执行边界。用户已说明主动停机，无需继续猜测外部终止原因或以新常驻服务规避停机意图。

CUA原会话已无登录页，本轮新标签页打开失败；不读取localStorage/JWT、不代用户登录或注册，不调用AI。下一次用户允许启动后仍需本人页面登录。B33的78回归/HTTP成功是06:16历史证据，不代表08:12以后系统在线。

## 恢复与后续四包

1. **等待用户明确允许重启**，然后核实自有服务/端口、PG正常恢复日志与只读一致性，不以不存在的健康检查跳过，保留AI paused/user。
2. 启动获准后补本人B32固定版本原价、双市场/期货K线只读浏览器验收；每项有预算、不循环打源，不下单或创建付费任务探活。
3. 可离线推进A股缺口原因/授权与补采预算、历史/PIT/复权/五年/基准契约；不造历史数据、假可得时间，不盲重导或写停机数据库。
4. 可离线推进股票只读Agent的Schema/引用/幂等/预算固定评测，模型原429未解除不反复请求；共享/期货生产代码继续冻结，正式发布/回退与人工验收未通过不宣称完整。

本轮只新增本检查点并更新计划指针，无生产文件修改/迁移/费用。运行回退不适用，因为没有启动或停止服务。不要把本记录的运行hold扩大为用户已经要求暂停全部开发，也不要删除全部续做自动化。
