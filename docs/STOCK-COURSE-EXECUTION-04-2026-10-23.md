# 课程整改执行04：固定版本读取与所选字段预检（2026-10-23）

## 状态与本轮四个切片

**部分完成；D2/D3/D4/R2不能标整包完成。** 按课程总包承接执行03，不重做九因子、原预处理或原研究引擎。本轮交付内部固定版本读取和只读查看链路的代码与离线验证，未将新router挂入生产股票router，也未将新组件挂入现有工作台。完整研究任务未接通时，不向真实用户开放半成品入口。

1. **固定观察版本的有界读取：已实现待验证。** Scope→原DatasetRef/manifest及来源Hash复核→字段/研究或暖机区间/固定证券池校验→观察日期集合→预先核对20万格诊断预算→按行批输出。保留Decimal字符串、小数成交量、真零、显式null和缺记录的区别；同字段冲突拒绝，不按采集时间选最新。重复读取同一固定版本确定一致。财务字段不能按报告行日期直接透视，必须后续经真实日历/PIT对齐。这是JSON诊断读取，不是Parquet、五年研究面板或异步研究任务。
2. **所选因子依赖/缺字段诊断：已实现待验证。** 九内置因子仅要求自身字段，动量不强制财务；自定义因子必须解析本人当前JWT盘保存的精确修订与表达式Hash，再取DSL依赖/回看长度。中性化/市值权重/轮动追加相应字段，不静默删所选因子。不合正值域的PE/PB/市值不作有效输入；未明确合并口径或仅公告日期而未绑定真实日历的财报不计可用。仅统计源观察记录，未给出逐交易日/逐证券覆盖率、实际暖机充分性或资格认证。所有数据仍observation_only，IC/回测始终关闭。
3. **本人固定版本目录与详情只读API：已实现待验证。** 独立`/research/v2`router，可信认证构造Scope、股票能力门禁、分页默认20/上限100、精确版本和资格匹配。列表不读全部源文件；详情只校验manifest及来源登记关系，不把源文件已删除的manifest描述成真实源健康。未迁移返回503、不建表；无产物根可以列元数据，但不能读取详情，不自动创建目录。8秒异步请求预算、504超时、统一脱敏错误/request_id/no-store，没有写入或模型调用。仅离线ASGI挂载测试，生产未挂载。
4. **固定版本只读查看组件与严格客户端：已实现待验证。** 分页目录→用户明确点击固定引用→版本/来源声明详情；刷新不切换最新版本，不自动选取。切用户/盘/Token同步遮蔽旧私有状态、取消请求且忽略迟到响应；失败不伪装零条。响应严格Schema/大小/身份核对，12秒取消，无自动重试。日期/证券数明确是清单声明，不是行情覆盖；IC/回测按钮固定禁用。仅组件交互测试，未挂现有页面/本人浏览器未验收。

这四项均是后续完整垂直功能的内部阶段，不能称页面已可使用、R2自定义研究已完成或课程验收通过。D1/D2/D3保留此前部分实现；D4仅开始读取前置，016任务/列式未实现；M1–M4的LightGBM仍未开始且仍必做。

## 文件与保护边界

唯一修改的既有生产源是`backend/app/services/stock_research_v2/archive.py`：允许元数据目录没有ArtifactStore；新增`describe_dataset`只验证manifest/源关系，`read_dataset`仍在此基础上验证全部源内容。详情请求另有512KiB清单预算，不改变原完整归档上限、v1序列化或旧结果。

新增九个保护范围文件：

- `backend/app/schemas/stock_dataset_inspection.py`：查询、因子引用/依赖、目录/详情/能力契约。
- `backend/app/services/stock_research_v2/panels.py`：精确观察值、有界网格、批次、缺失/冲突诊断。
- `backend/app/services/stock_research_v2/qualification.py`：所选字段/自定义修订的内部预检；不授予研究资格。
- `backend/app/services/stock_research_v2/datasets.py`：元数据目录、清单详情与存储可读能力。
- `backend/app/api/stock_research_v2.py`：独立未挂载的只读认证router。
- `frontend/src/lib/stock-dataset-api.ts`及同目录`stock-dataset-api.test.mjs`。
- `frontend/src/components/factor-research/stock-dataset-picker.tsx`及同目录`stock-dataset-picker.test.mjs`。

新增后端测试三文件：`test_stock_research_panels_v2.py`、`test_stock_dataset_qualification.py`、`test_stock_dataset_catalog_api.py`，均位于`backend/tests/`。另更新本检查点、根AGENTS及三个计划的续做状态，不扩写另一套总计划。

开工执行03清单1104零漂移。完工对原清单verify按预期报告9新增/1修改/0删除（原清单不应伪报全绿），由明确股票白名单二次断言，原1103其他文件SHA保持。5份既有源/文档字节备份Hash复核通过，archive原备份Hash与执行03清单一致。新完整清单1113文件用原guard复核零漂移。见`logs/stock-course-execution-04-backups.json`、`logs/stock-course-execution-04-preservation.json`、`logs/stock-course-execution-04-current-inventory.json`、`logs/stock-course-execution-04-preservation-final.log`。旧baseline/备份不覆盖，未回退旧用户修改。

期货、共享认证/网关/布局/运行配置/依赖锁、现有股票router/页面、015/017迁移和旧金融公式均未修改。没有提交、建分支或新增仓库。该Hash结论只证明相对本轮开工状态不变，不能据此声称所有期货业务已在线成功。

## 真实验证

Python统一`backend/.venv/Scripts/python.exe -X utf8`；最终后端命令：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_dataset_catalog_api.py backend/tests/test_stock_dataset_qualification.py backend/tests/test_stock_research_panels_v2.py backend/tests/test_stock_research_archive_v2.py backend/tests/test_stock_point_in_time_v2.py backend/tests/test_stock_universe_v2.py backend/tests/test_stock_research_dataset_migration.py backend/tests/test_stock_custom_factors_api.py backend/tests/test_stock_custom_factors_migration.py backend/tests/test_stock_factor_expressions.py backend/tests/test_stock_provider_contract.py backend/tests/test_stock_research_artifact_store.py backend/tests/test_stock_metrics_v2.py backend/tests/test_stock_research_engine.py backend/tests/test_stock_research_regressions.py backend/tests/test_stock_research_dependencies.py backend/tests/test_stock_research_snapshots.py backend/tests/test_stock_report_artifacts.py backend/tests/test_stock_report_bookmarks.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
```

**218 passed，252条现有User模型datetime.utcnow弃用警告，exit0**，无skip，日志`logs/stock-course-execution-04-regression-final.log`。新三文件共16项（含参数化边界）被纳入此结果。离线SQLite现有夹具、真实QA临时文件、ASGI内存传输验证，不是运行PG或真实HTTP服务，也不是供源认证。一次定向自审补了原先误计的财报口径/日期证据、根目录被移走/改文件、超时错误契约，未重审全仓。

前端工作目录`frontend`实际命令：

```powershell
$tests = @(Get-ChildItem src -Recurse -File -Filter *.test.mjs | ForEach-Object { $_.FullName })
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @tests
node node_modules/typescript/bin/tsc --noEmit
```

**678 passed，0失败/跳过/待办；TSC exit0**。日志`logs/stock-course-execution-04-frontend-final.log`、`logs/stock-course-execution-04-tsc-final.log`，其中新客户端4项、组件5项。组件夹具替代网络/React渲染环境，不替代真实本人页面或核心数据库。

保护命令：

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-03-current-inventory.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py snapshot logs/stock-course-execution-04-current-inventory.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-04-current-inventory.json
```

第一条完工返回1是已声明股票差异，不是冻结文件漂移；二次精确白名单/备份断言证据保存preservation.json，新清单verify exit0。运行Python importlib.find_spec实际确认pyarrow/fastparquet/lightgbm均未安装，本轮不为列式进度修改共享依赖或把JSON冒充Parquet。

### 失败与修复保留

- reader/qualification初红为新增模块/Schema尚不存在，API初红为router尚不存在，前端client/picker初红为文件尚不存在，日志`...-reader-red.log`、`...-api-red.log`、`...-client-red.log`、`...-picker-red.log`；不将这些收集失败称金融行为证明。
- 初次综合API31通过1失败：测试把动态GET资源上的POST预期为404，但框架实际405。修正为断言405且无任何POST路由，未增加写能力或放宽金融/权限检查，`...-api-first.log`。
- 边界初红4失败含财报夹具单位写错ratio，被已有Schema正确拒绝。夹具改为既定percent后，真实行为红仍4失败：根目录失效仍详情enabled、vendor/parent/仅日期财报误计eligible。保留`...-boundary-red.log`及`...-boundary-behavior-red.log`，实现拒计/能力检查后通过。
- 超时映射初红503≠504，`...-timeout-red.log`；修为独立TimeoutError→504和request_id后最终回归通过。

**未执行：** 实库PG/真实HTTP/浏览器、015/017应用/回退、完整构建、全市场真实采集/来源认证、Parquet/300股五年5分钟性能、完整新IC/回测/ML。停机门禁未解除，没有临时端口或另启数据库绕过。

## 金融口径、资源与兼容回退

没有修改旧因子/IC/收益/费税/成交公式。本轮新增内部诊断方法版本`stock-observation-panel-v1`、`stock-observation-requirements-v1`；PE/PB/市值正值域检查只作用新观察读取/依赖统计。合并财报/日期精度保守处理只作预检拒计，不自动猜历史时间或重写源记录；不代替真正PIT最终前沿选择。单字段源记录计数包含不同修订，不是去重有效样本或历史回归输入。行业历史/日历/授权证据/可成交能力始终有阻断项。

诊断网格20万格上限在分配cells之前核验，batch_rows最大1000，来源仍受原64MiB/100万行/1000源上限；按源复读JSON是当前诊断策略，不保证五年性能。未删除原200万研究面板保护。内部同步文件校验受清单/单文件字节预算，但网络盘等异常IO尾延迟仍需部署测试，8秒asyncio不能宣称能强制中断所有同步文件IO；正式研究必须转后台任务/列式读取。

无新增数据库结构、无迁移应用、无历史回填。015/017未应用状态不变，016/018/019未创建。数据表仍强制observation_only，不允许通过请求升级资格。完整来源read_dataset保留原Hash/所属用户/盘/关系验证；目录describe刻意只读manifest，契约与页面明确源文件未检查。

回退当前不涉及新公开请求：保留旧API/页面，按字节备份恢复archive的精确股票改动，停止引用新内部模块；不得删原归档、表、文件或数据来回退。未来公开前先完成独立存储根/权限配置、015/017真实隔离验证、D4完整链路和灰度设计；本轮没有新增配置读取或运行app.state注入。物理根被移走不重建，Hash失败不换最新版本，孤儿仍不授权。

外部模型/数据调用0，新增这两类费用0，未安装依赖、未读取/输出密钥、未恢复付费AI、未写模拟账户。服务保持用户停机，自动化不能解除门禁。

## 人工路径与下一四包

当前用户页面没有新固定版本入口，因此不要求用户立即人工点击，也不称用户已可体验新功能。恢复获得明确许可后，先正常核实原库并完成私有隔离PG迁移/事务/权限测试，再以授权小批真实来源登记固定manifest；本人登录检查仅所属盘分页目录、明确选择旧Hash、清单声明/来源未认证提示、损坏失败/跨用户与跨盘拒绝、会话变更清理；完整D4前IC/回测应始终关闭。同期执行冻结期货关键只读回归和正式回退演练。

下一四包保持原依赖，不另立总计划：
1. D4列式基础：单独依赖兼容/许可证/锁版设计，有明确股票隔离安装路径后实现真正Parquet分区/列裁剪；不以当前JSON诊断替代。
2. D4任务切片：独立016任务记录、租约/幂等/取消/超时/恢复和私有结果登记，固定DatasetRef贯穿worker；离线实现但运行保持关门。
3. D3/R2接线：绑定真实来源证据/历史日历与成员版本、PIT对齐/暖机与所选字段资格，再把精确自定义FactorRef接原预处理/IC，持久化可复现结果；未核验真实源保持不可认证，不猜补。
4. 验证驱动与后续：补私有PG迁移/故障/权限验证代码；获允许运行后执行实库/HTTP/浏览器。之后继续R3/R4和必做LightGBM，不把本轮内部前置算完整核心交付。
