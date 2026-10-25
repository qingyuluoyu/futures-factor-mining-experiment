# 课程整改执行02：自定义因子定义管理（2026-10-23）

## 状态与本轮三个业务子包

**R2部分完成，不能标记整包完成或发布可用。** 本轮遵照用户自主执行要求推进三个同目标垂直子包，没有再次重写总计划：

1. **保存与编辑定义：已实现待验证。** 股票研究页用户显式打开编辑器→JWT用户/盘与股票市场校验→严格Schema/有界AST校验→事务保存定义、不可变新版本及幂等回执→返回当前版本→页面显示版本/表达式摘要；编辑用expected_revision，旧版本不覆盖。离线正常/非法/权限/重复/冲突通过；真实PG及本人页面未验收。
2. **历史查阅与停用：已实现待验证。** 所属用户/盘分页列表和版本历史、指定原版本读取；停用需页面二次确认，禁止新编辑/新选用，旧定义保留，内部历史恢复必须显式allow_disabled且仍检查所属用户/盘。历史查看不是新计算；未把自定义ID塞给旧v1回测。离线通过；PG FK/并发与本人页面未验收。
3. **写入恢复与安全开放：已实现待验证。** 原键同参返回原回执，同键异操作/异参409；PG事务级所属用户/盘advisory lock与版本检查；客户端网络不确定时保留原命令/键、锁住草稿并仅显式重试原请求，不自动重试模型/计算。切用户/盘/token立即隐藏旧草稿、取消未完请求并忽略晚到响应。未迁移/校验和不匹配时禁止定义写入，未接固定数据研究时research_enabled固定false。离线通过；PG并发和真实网络故障恢复未验收。

原17包不增减：R1/D1/D2保留执行01的部分实现；D2的元数据注册、D4固定快照/任务、R2自定义IC/回测、ML本轮未推进，不将其算进完成数。016/015/018/019仍未创建。未完成链路不暴露为可运行策略。

## 修改范围与保护

既有源/测试只改3处：
- `backend/app/api/stock_factors.py`：只兼容挂载股票独立v2子路由，原API/默认值/计算未改。
- `frontend/src/components/factor-research/factor-research-workspace.tsx`：新增显式打开编辑器按钮和当前token/scope传递；默认不发新增请求，不启动研究。
- `frontend/src/components/factor-research/factor-research-page.test.mjs`：增加懒加载入口/零任务提交测试及新组件替身，不放宽原断言。

新增实现：
- `backend/app/models/stock_custom_factor.py`：独立DeclarativeBase，定义头/不可变版本/幂等回执/迁移版本表，不加入共享启动create_all。
- `backend/app/schemas/stock_custom_factor.py`：创建/编辑/停用/校验/结果/分页Schema。
- `backend/app/services/stock_research_v2/custom_factors.py`：白名单字段元数据、迁移readiness、所属用户/盘作用域、分页、事务编排、固定原版本resolve。
- `backend/app/api/stock_custom_factors.py`：股票v2定义API、8秒服务预算、结构化错误、request_id、no-store、无私有表达式日志。
- `backend/migrations/20261023_017_stock_custom_factors.sql`、`backend/scripts/migrate_stock_custom_factors.py`：兼容新增迁移及显式只读检查/应用工具。
- `frontend/src/lib/stock-custom-factor-api.ts`：Zod请求/响应校验、12秒请求超时、1MiB流式响应上限、ID/版本/分页对账、显式原键写入。
- `frontend/src/components/factor-research/stock-factor-editor.tsx`：创建/编辑、语法/字段说明、版本历史、停用确认、原请求恢复及会话隔离。

新增测试：`backend/tests/test_stock_custom_factors_api.py`、`backend/tests/test_stock_custom_factors_migration.py`、`frontend/src/lib/stock-custom-factor-api.test.mjs`、`frontend/src/components/factor-research/stock-factor-editor.test.mjs`。

只更新本检查点、AGENTS续做指针及课程/原主计划/研究子计划的真实状态；没改共享认证、网关、布局、运行配置、原金融引擎或期货代码。相对执行01保护清单，声明3修改/10新增/0删除，其余1084文件SHA不变；新完整清单1097。原清单不覆盖，差异对账/7个既有文件字节备份见`logs/stock-course-execution-02-preservation.json`及`logs/stock-course-execution-02-backups.json`。

## API与金融口径

统一股票根下`/api/stock/research/v2/custom-factors`：GET availability/列表，POST validate/创建，PUT /{id}编辑，POST /{id}/disable，GET /{id}/versions及/{id}/versions/{revision}。列表默认20、最大100，offset最大100000；稳定UUID/版本排序，一条CTE取数量与分页，避免分页总数分裂。写入UUID键在owner/mode作用域内唯一，操作/目标/严格Schema后的JSON均纳入请求摘要；跨操作复用同键也409。无使用次数限制。

保存不代表已验证经济有效性或可交易；表达式规范化AST及SHA固定，方向/名称/经济含义随revision保留。已有DSL方法stock-expression-v1不改；字段close为统一锚点价格，财务/换手字段百分数、市值CNY，依赖的真实单位/公告可得时间/正值域必须由D4/R1实际面板加载器检查后才能进入研究。当前说明是契约，不是已经认证了历史数据。resolve只解析原版本，不加载市场数据、不自动中性化/填充，也不发任务。除零/log域/缺历史的原有NaN政策不改，旧v1报告/公式/费用不重算。

JWT的virtual/live仅保留现有登录盘作用域兼容性，定义管理无下单能力，任何盘均不接真实资金。停用不是删除：新选用失败，历史原版本仍可按所属权限恢复。原键重放返回原写入回执，不承诺代表此刻最新状态；页面写后单独刷新所属列表。

## 实际验证命令与结果

Python统一`backend/.venv/Scripts/python.exe -X utf8`。最终实际执行：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_custom_factors_api.py backend/tests/test_stock_custom_factors_migration.py backend/tests/test_stock_factor_expressions.py backend/tests/test_stock_provider_contract.py backend/tests/test_stock_research_artifact_store.py backend/tests/test_stock_metrics_v2.py backend/tests/test_stock_research_engine.py backend/tests/test_stock_research_regressions.py backend/tests/test_stock_research_dependencies.py backend/tests/test_stock_research_snapshots.py backend/tests/test_stock_report_artifacts.py backend/tests/test_stock_report_bookmarks.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
```

**167 passed，156条现有User模型datetime.utcnow弃用警告，exit0**，`logs/stock-course-execution-02-regression-final.log`。目标定义/API/迁移静态8测试通过。API用真实FastAPI路由/服务和既有离线SQLite夹具，HTTPX ASGITransport不启动监听；不等于真实PG、运行HTTP或发布证明。迁移SQL未执行：静态只检查新增语句、模型字段/约束名、PG DDL编译、反射CHECK正规化等价与非等价；不假称应用/回退已实测。

在`frontend`实际执行：

```powershell
$files=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName)
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files
node node_modules/typescript/bin/tsc --noEmit
```

**669 passed，0 failed/cancelled/skipped/todo，exit0；TSC exit0**，`logs/stock-course-execution-02-frontend-final.log`、`logs/stock-course-execution-02-tsc-final.log`。新客户端5测试、编辑器5测试、既有工作台新增懒入口测试。UI为既有VM钩子夹具，不等于浏览器像素/交互验收。

先红后绿证据保留：`...-backend-red.log`是未接路由/缺模块4失败，`...-editor-red.log`是编辑器不存在4失败，`...-entry-red.log`是入口缺失1失败，`...-client-red.log`是客户端不存在的导入失败。后续行为红`...-boundaries-red.log`为401/市场错误未结构化2失败、`...-revision-red.log`为合法末版本读取被拒、`...-ref-error-red.log`为固定引用错误类型不符、`...-disable-bound-red.log`为合法末版本停用被拒；均已修复。首次API实现2失败原因是SQLite共享连接中再次inspect导致待提交写回滚，改为在已校验事务内直接读取刚写版本，不放松断言；另有测试补丁插错块的NameError及静态扫描把ON DELETE RESTRICT误认为DELETE语句，修复测试定位/语句首检测，未删测试/跳过/硬编码生产结果。

`scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-01-current-inventory.json`最初通过1087零漂移；开发后对旧清单返回failed是预期声明差异（3改10增），必须看精确对账，不掩盖为零变化。完成对账后新增完整清单，再用原verify确认1097零漂移；差异/备份/命令证据见preservation日志。

未执行：真实PG017 check/apply/重复应用/并发/约束/回退、线上HTTP/JWT本人浏览器、完整构建、五年性能/ML、外部源/模型请求。没有用离线检查代替这些验收。

## 迁移、回退、运行与成本

017只新增三业务表及版本表，不改旧表/旧索引/旧数据；FK引用现有users、定义版本引用定义头，ON DELETE RESTRICT，PG advisory锁、5秒锁等待/30秒语句超时，整事务提交版本校验和。工具默认check只读，apply必须显式参数；有版本冲突/不完整旧表时失败，不擅自DROP/修补。完整验证列/类型/空值/默认/PK/FK/命名CHECK/索引及convalidated。**本轮未应用**。

获准恢复后拟执行、但本轮未执行的命令：`backend/.venv/Scripts/python.exe -X utf8 backend/scripts/migrate_stock_custom_factors.py --check`；只在隔离PG先按发布流程实测后，才可显式`--apply`。遵守原5433专用回环目标；不得拿生产未备份实例作实验。独立版本表不要求先应用未实现的015/016。

回退应用代码时只移除股票独立路由挂载/新入口，保留所有版本/回执表，旧v1仍可读旧报告。若已有真实新定义写入，不得整表删库“回退”；停新写后兼容前向修复或独立发布，不假称表删除可恢复。字节备份用于精确参考/patch，不能直接整文件覆盖后续别人修改。当前未应用迁移无业务DB回退操作。

本轮未启动、停止、重启任何服务，也未恢复AI任务。只读监听核对发现127.0.0.1:3000已有PID10448监听，未操作该进程、未据此认定是本项目或当作在线验收。仍服从用户不重启约束。没有源/付费模型请求、依赖安装、批量导数或资金操作，新增外部调用成本0。一次AST/列表/API有参数、响应、运行时间边界，没有日/月/累计使用次数门槛。

已更新本次触发的09:50续做自动化：优先最新课程计划/执行检查点，明确期货冻结/用户运行hold/0预算；原时间、名称、ACTIVE和所属对话保留，未因任务未完而删除，其余自动化未改。

## 人工路径与下一四包

当前不要求用户启动。获明确恢复许可并完成真实PG迁移验收后，本人登录A股→因子研究→自定义因子定义管理→填写名称/`close / lag(close,20) - 1`/方向/含义→校验（不保存）→显式保存v1→改公式校验后保存v2→查看原v1→确认停用→历史仍可读。另一用户/盘应看不到该定义；期货不会出现此入口。模拟断网后仅重试原请求，刷新前先保留请求状态；页面完全关闭会清掉内存草稿/原键，重新进入先查询已存列表，不能据没有本地草稿断言服务端未写入。

剩余风险：PG真实并发/事务/FK与反射兼容、网络提交回执丢失跨页面重进恢复尚未验收；客户端原键恢复目前只保证当前挂载会话内，尚无持久私有操作日志；编辑器视觉/长公式移动端未浏览器验收；定义本身不能用于v1计算。数据覆盖/PIT/退市/公司行动/原始价/锚点/可信基准/五年计算/Parquet/LightGBM等原缺口不因本轮CRUD消失。

下一四包按依赖执行：
1. D2：真实产物元数据注册/所有权/不可变manifest、015兼容迁移；保留现有ArtifactStore，不盲导历史。
2. D4：DatasetRef/固定快照读和资格分层、016异步任务及失败关闭；缺数据/来源证据明确阻塞，不伪造资格。
3. R1/R2：绑定固定DatasetRef和原FactorRef、方向/单位/PIT域检查、复用原预处理与IC的确定性计算，先离线金融测试；自定义IC完整前继续关闭研究入口。
4. R2链路与交付：接新的异步任务/保存结果/原版本恢复和研究页配置；补私有写入恢复持久方案。真实PG/HTTP/浏览器/迁移仅在获恢复许可后执行，之后再做R3/R4、ML必做链路，不跳过。
