# B25 固定盘后版本缺口对账：真实检查点

## 状态与范围

三包股票独立只读运维切片已实现并通过受影响单测、真实隔离PG和实际已有数据核验；不是新增用户页面、股票AI开放、行情补齐或系统整体完成。原正常生命周期服务未重启，期货/共享生产文件、配置、模型、任务暂停状态、订单和资金没有修改。没有生产DDL、新依赖、提交或分支。

实际新增文件：

- `backend/app/services/stock_eod_reconciliation.py`：确定性成员转移、当前目录注释、来源/内容验证和有界固定版本读取。
- `scripts/audit-stock-eod-reconciliation.py`：仅专用本地PG5433的受信任操作员CLI；明确两个SHA、只读可重复读事务、30秒整体/8秒语句/2秒锁超时、无重试；仅落本机独占JSON和安全状态/trace/path。
- `backend/tests/test_stock_eod_reconciliation.py`、`backend/tests/test_stock_eod_reconciliation_cli.py`：正常/异常、输入边界、稳定hash、无隐私证据及错误脱敏。
- `scripts/test-stock-eod-reconciliation-postgres.py`：真实私有schema测试，不向public创建测试用户/订单/资金或行情。
- 本记录、详细B25计划及主计划/期货保护计划的检查点链接。

## 金融/数据口径与实际发现

读取固定版本，而不是自动换成latest：

- 20日：`087651ac5cead93b4d3be4e826fc21ae64334e2f0a9f942252cbc3c7d33d3580`，5549/5558、缺9、未知19。
- 21日：`6934904d5b088eecb25f839219ee9a85a0d5f6278d49e02bb0bd42a9870c3245`，5548/5558、缺10、未知20。
- 两天原始目录hash及当前按对应日期投影的目录hash相同：`2f726d9bc510d80e9384e145015d8b638f65630362f76e990d8b63201747f532`。因此这两日的缺口差异不能归因于本次读取时目录漂移；**不能据此认定供应商目录完整、证券性质/停牌原因或历史PIT已证实**。
- 旧9缺和19未知均延续，无已消失缺口。新增缺行情为`601326.SH`（当前目录秦港股份），从20日matched_quote变21日missing；新增未知为`920296.BJ`，从20日outside_snapshot变21日unknown，当前目录仍absent_now。没有拿20日价格填21日，没有将未知直接加进有效报价。
- 其余持续缺行情：000016、002388、002717、002731、301314、301468、601059、601198、688496。未知完整20代码在本地对账文件，不武断归为新股/退市/非A股。
- 仅已存供应商原价盘后快照，CNY、不复权、量单位100股一手；不拼接旧qfq，不生成交易价，不推断公告/可得时间。缺口消失仅标no_longer_flagged，需查看after是否matched_quote、unknown或outside_snapshot，不能一律称修复。
- 原数据证据只返回是否存在，不回传证据JSON、API凭据或原始错误文字；当前目录始终标“现在”，不替代原快照成员关系。非独立真实性认证、非PIT/回测资格、非交易日连续性证明。

实际报告：`logs/stock-eod-reconciliation-514592e2257b4f5fa6d43953da514662.json`；30个受影响代码，结果内容hash`eb07186e70eaf6c6d65018a9a7d272318f3f3e604ef58489399799d5f028dd32`。与首次真实对账`ff92bfd78756488b8c350f53b982c36a`内容hash相同，生成时间/请求ID各自独立，不混分页/版本。只读allowlist来源元数据`logs/stock-b25-source-mode.json`显示两日均原live_http且各记录3响应；本轮没有重新调用这3个响应或验证上游当前返回。

## 实际命令与验证

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_eod_reconciliation.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_eod_reconciliation_cli.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_eod_reconciliation.py backend/tests/test_stock_eod_reconciliation_cli.py backend/tests/test_stock_eod_gaps.py backend/tests/test_stock_daily_archive.py backend/tests/test_stock_raw_panel.py backend/tests/test_full_local_backend.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-eod-reconciliation-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-stock-eod-reconciliation.py --previous 087651ac5cead93b4d3be4e826fc21ae64334e2f0a9f942252cbc3c7d33d3580 --current 6934904d5b088eecb25f839219ee9a85a0d5f6278d49e02bb0bd42a9870c3245
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-full-startup-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py snapshot logs/futures-preservation-b25-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b25-final.json
```

- 初始服务16红（模块缺失断言，不是导入收集错误），CLI9红（入口缺失）；实现25绿。自查补缺/空时间、交易所错误类型三反例先3红后绿。只读审查发现无效时间字符串可能在服务异常文字回显原值，追加1红后统一固定安全信息；审查复核闭合，无剩余Critical/Important/Minor。保留`stock-b25-red.log`、`stock-b25-cli-red.log`、`stock-b25-validation-red.log`、`stock-b25-review-red.log`。
- 最终受影响**109通过、1既有datetime.utcnow弃用警告**，`logs/stock-b25-affected-review-final.log`。
- 真实私有PG**14检查通过**并清理，最终`logs/stock-b25-private-pg-review-final.log`、`logs/stock-eod-reconciliation-ffff50a6319948c1b503070d885f758a.json`：固定版本/缺失/重复/逆序拒绝、真实只读拒绝写25006、并发目录更新在同事务稳定、新事务显式变化、坏快照失败、public源/目录/AI任务摘要不变。不是生产JWT/用户HTTP授权验收；CLI本身不暴露网络写接口。
- 首轮完整后端**2769通过、3既有skip、741warnings**（134.34秒），`logs/stock-b25-backend-full.log`；在补最后四个反例前收集，不能冒充最终新增反例全量结果。最后代码/审查修复后重新完整执行，**2773通过、3既有skip、741warnings**（124.90秒），`logs/stock-b25-backend-final.log`；没有测试失败，不把原跳过或警告说成全部功能已验收。
- 原运行HTTP核验**6检查通过**，`logs/stock-b25-running-http.json`：登录页/前端注册状态/后端健康200，股票缺口匿名401，preview404，原main/scheduler/研究/EOD/归档worker保持运行；没有登录、交易、付费对话或新上游请求。3000/8002/6380/5433/8110原PID监听未更换。健康不是全部金融功能认证。
- 最终再核验`logs/stock-b25-final-runtime-pause.json`：唯一既有AI任务仍paused/user，原正常生命周期及scheduler/研究/EOD/归档worker仍true；1018保护再次零漂移。没有恢复收费任务或将GP关闭状态伪报已开启。
- 原1016基线本轮开工通过；结尾旧清单verify如实报告**仅新增两文件**（新股票服务及audit CLI）、无已有文件changed/removed，`logs/stock-b25-original-preservation.log`。新扩展**1018**清单零漂移；旧清单未覆盖、未伪称旧verify完全相同。
- 未执行本轮Node/TSC/浏览器操作/生产build/部署验收：未修改前端或挂用户入口；已有预览登录不作为本轮本人功能验收。没有关闭检查或新加skip；全回归原3个skip保留，不强制额外真实付费模型调用。

## 成本、兼容与回退

本轮新增产品模型调用0、上游请求0、public业务写0；只读SQL/本地CPU和私有schema测试成本。正常已运行调度的历史来源费用未测，不能宣称供应商免费。没有取消任何金融/权限/并发/预算边界。用户授权的AI任务仍手动暂停，不恢复自动扣费。

没有数据库迁移或旧接口变更。仅新增未挂用户入口的运维工具，无需重启/修改现有运行进程；回退是停止使用新CLI和模块，保留来源与对账证据，不删除真实盘后批次或改回旧快照。受信任本地数据库操作员资格不等同于普通用户获准读取所有私有业务数据；工具仅选择公共市场目录/快照，不读取用户凭据、资金或持仓。

## 人工入口、剩余风险与下一三包

操作员运行上面两个明确SHA的CLI，按stdout路径打开JSON，核对gap_sets、各行before/after和目录hash；用户行情页已有缺口浏览器继续原样。报告是定位证据，不是已补齐行情。

来源原因尚未证实，20个未知证券目录、10缺行情不得靠猜测、删样本、补零或大批重导“修复”。旧历史/PIT/qfq锚点、五年区间、可信基准、DSL、回放扩池、股票只读AI及正式构建/发布/人工双市场验收仍未完成。

下一三包：①依据本次代码清单设计最多3只/日的授权来源核对与费用预算，预算/原因未明确前不请求或改目录；②股票独立只读AI的持久幂等/上下文/费用限制及固定评测，不解冻共享聊天写工具；③真实历史/PIT/复权锚点和可信基准/回放资格，再单独安排发布回退及本人验收。整体仍部分完成，保留续做自动化。
