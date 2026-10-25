# 第四批真实交付及本地运行记录（2026-10-19）

## 当前状态

**系统整体仍部分完成；本批三项研究报告切片已实现并通过隔离链路验证，追加安全启动切片已启动最新本地实例。** 不声称全站金融/权限/数据、国际金银或生产发布已验收。

子计划：`docs/superpowers/plans/2026-10-19-stock-completion-batch-04.md`。用户追加“项目完整运行一下”后，核实旧服务已停止，才启动专用本地服务，没有强杀未知端口占用者。

## 四个工作包

### B04-1 保存报告导出

- 新只读 `GET /api/stock/research/jobs/{job_id}/export?format=json|csv`，复用真实 JWT、用户/登录盘资源权限和匹配迁移就绪检查。pending/failed/无结果拒绝，其他用户/盘404。
- JSON保存原job、参数、方法、结果、数据清单；CSV涵盖全部**已经保存**的参数/指标/净值/IC/分组/交易，而不是只导出屏幕60条。旧计算最多保存500条的截断事实不能恢复，原 `trades_total/trades_truncated` 不改。
- 中文/BOM、逗号换行、代码前导0字节、危险字符串公式前缀、真实负数、固定UUID文件名、nosniff/no-store/request_id均有测试。CSV的代码列导入表格软件时应按文本导入，CSV不提供Excel单元格类型保证。
- 保存结果8MiB、导出16MiB；客户端实际流读取同样限额，错误响应64KiB上限/错误文本280字符，保留HTTP状态。20秒请求边界、单飞/取消、当前会话校验和ObjectURL回收。
- 历史可视化解析失败的旧报告仍保留导出入口（前提后端能力已部署），不能因新展示schema把旧文件锁死。

### B04-2 两份保存报告对照

- 新只读 `GET /api/stock/research/compare?left_id=UUID&right_id=UUID`，两份不同、同kind、本人同盘、完成且有结果的研究。
- 保存方法/流程/64位数据指纹/标准化请求区间一致才提供差值，否则原值并列且delta=null。没有重算、基准、超额收益或A/B因果认证。
- 差值**右减左**。已有 `_pct` 及win_rate已是百分数，前端不再乘100；百分比差值为百分点，IC/夏普/换手保持原单位，未知不补0。
- 指标最多32、政策100、每条曲线10000点、响应2MiB；非有限/错误类型/重复乱序日期/非正净值等明确拒绝，不截断伪装成功。
- 历史页勾选同类型完成报告、最多两份，跨分页保留且可清空；展示版本/参数/警告和各自日期净值，不拼接假基准。换用户、盘、token、同token新登录revision或换选择时丢弃迟到响应。

### B04-3 历史配置恢复

- 历史详情 → `/factor-research?restore=UUID` → 真实owner/mode GET → 展示原方法/参数 → 当前schema和因子目录校验 → 明确点击“应用到新研究表单”。
- 完整回填日期、因子、预处理、horizon/groups、周/月、持股数、权重、费滑；重新运行覆盖预检。应用不创建、不启动、不复用原幂等键，不声称恢复旧方法或同版本复算。
- 新 `recoveryPending` 保护初始化、未知提交、恢复查询失败/损坏记录；未核对前既不能覆盖配置，也不能新建。显示真实原因，继续沿原幂等键查询/重试，不丢弃未知提交。
- 同任务轮询不再覆盖已编辑表单；显式历史配置不被迟到的旧任务参数覆盖。恢复组件、任务hook及工作区key加入sessionRevision/market防护。

### B04-4 无破坏性自动DDL的本地启动

- 原 `main.lifespan` 不再调用 `_init_tables`（该旧函数含DROP COLUMN、任务状态UPDATE、历史市场重分类）。保留旧函数用于后续逐项迁移盘点，本批没有执行它。
- 改用 `verify_database_schema`：注册原有模型，PG只读事务，10秒statement/2秒lock/外层15秒超时，检查已存在表和列。缺失则拒绝启动，处置引擎，不启动Redis或worker、不自动create_all/回填/删列。
- 这只是**表/列存在接口校验**，不是类型、约束、索引、数据版本或完整P01基线迁移认证。新空库仍须后续正式版本化基线，不能靠启动自动建库。
- 无数据库结构变更、无数据迁移。真实本地PG校验39表/496列；缺表和缺列反例均拒绝，再校验原库仍通过。
- 当前通过原 `scripts/run-local.ps1` 隐藏启动：专用PG5433、Redis6380、后端8002、Sina桥8110、前端3000。`logs/local-services.json` 记录进程；不要根据过期PID强杀进程。

## 能力发布兼容

- 研究availability新增兼容布尔字段 `saved_report_artifacts`：必须schema就绪且导出/对照路由均已登记才为true。前端缺字段默认false，旧后端不暴露死按钮，旧报告查看和配置读取仍可用。
- 主执行者复查修复Next股票代理：用ArrayBuffer保留BOM原字节，保留安全附件头/nosniff/方法/追踪，仍不透传Set-Cookie。不是只在ASGI上下载成功。
- 本地最新后端已真实加载路由；不是仍运行旧源码的健康200。匿名经Next调用研究availability返回401，未绕过认证。

## 实际修改文件

后端产品：

- 新 `backend/app/api/stock_research_reports.py`
- 新 `backend/app/services/stock_report_artifacts.py`
- 新 `backend/app/schemas/stock_report_artifacts.py`
- 新 `backend/app/services/database_readiness.py`
- `backend/app/api/stock_research_jobs.py`（兼容能力字段）
- `backend/app/main.py`（路由登记、只读启动门）

前端产品：

- 新 `frontend/src/lib/stock-report-artifacts.ts`
- 新 `frontend/src/components/factor-research/research-report-actions.tsx`
- 新 `frontend/src/components/factor-research/research-comparison-panel.tsx`
- 新 `frontend/src/components/factor-research/research-restore-panel.tsx`
- `frontend/src/components/factor-research/stock-research-history.tsx`
- `frontend/src/components/factor-research/factor-research-workspace.tsx`
- `frontend/src/components/factor-research/use-research-task.ts`
- `frontend/src/components/factor-research/stock-research-entry.tsx`
- `frontend/src/components/factor-research/research-job-panel.tsx`
- `frontend/src/lib/stock-research-api.ts`
- `frontend/src/lib/stock-proxy-response.ts`
- `frontend/src/app/api/stock/[...path]/route.ts`

测试与工具：

- 新 `backend/tests/test_stock_report_artifacts.py`、`backend/tests/test_database_startup_readiness.py`；更新 `test_stock_research_jobs_api.py`、`test_stock_eod_lifecycle.py`。
- 新前端actions/comparison/restore/client `.test.mjs`、`report-artifact-test-harness.mjs`；更新history/workspace/hook/entry/job-panel/API/proxy测试。
- 新 `scripts/test-stock-completion-batch-04-postgres.py`；B02脚本仅report_label白名单增加b04。
- 本批子计划、主计划最新检查点、本记录。无Git元数据，没有提交/建分支；没有读取或输出用户密钥。
- `README.md`、`docs/SELF-HOSTING.md`：移除自动建表启动指引，明确现库检查、新空库基线仍未完成，避免旧文档诱导调用遗留破坏性初始化。

## 真实验证证据

### 红绿及复查

- 后端新报告初始16个缺router red；实际大小/错误安全头2 red、未知指纹1 red后修复。目标报告/API/service最终37通过；日志 `logs/stock-batch04-backend-*.log`。
- 客户端4 red→4 green，导出按钮3 red→3 green；新comparison缺组件及同token重登录锁按钮共5 red后通过，历史入口/同token新会话3 red后通过。
- 恢复首轮panel 7 red、workspace 5 red、hook 3 red后52通过；会话revision追加反例后57通过。`logs/batch04-restore-*.log`。
- 主复查发现百分数展示再次乘100、Next代理BOM和附件头丢失、旧结果解析失败隐藏下载、非2xx响应无限读、未核对恢复时新建按钮看似可点，均有新增反例并修复。
- capability后端2 red、前端2 red后修复；FastAPI当前使用嵌套IncludedRouter，直接扫app.routes未正确发现路由，改用公开 `url_path_for`，没有依赖私有对象。原workspace测试夹具补充已部署capability，没有放宽入口断言。
- 只读启动初始3 red；首次SQLite测试误依赖未安装aiosqlite，改沿本项目已有同步SQLite+异步适配模式，真实SQL没有被mock。当前启动/生命周期/启动器目标26通过。
- 加入启动门后的全后端首轮12失败/2438通过/3既有skip：EOD生命周期原夹具仅mock旧_init_tables，假Engine没有connect；修正夹具对新外部依赖的替身，保留旧迁移绝不能执行断言，另加顺序及失败处置测试。失败XML保留 `logs/stock-completion-b04-startup-transition-failed.xml`，不是删除或跳过用例。
- 独立前端复查给出旧报告入口、错误body限额两项P2，已补红绿修复；独立后端复查未完成全部结论（执行者额度中断），其余由主执行者逐段复查。不能称双域独立复查全部通过。

### 命令及结果

1. 根目录 `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q --junitxml=logs/stock-completion-b04-backend-final.xml`：**最终2452通过、3既有跳过、620警告、246.13秒，退出0**，`logs/stock-completion-b04-backend-final.log`；没有新增skip、放宽关键断言或用追加启动门之前的2447通过代替。
2. 前端目录 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)`：**406通过、0失败/跳过**，`logs/stock-completion-b04-node-final.log`。
3. 前端目录 `node node_modules/typescript/bin/tsc --noEmit --incremental false`：退出0，`logs/stock-completion-b04-tsc-final.log`。
4. 根目录 `backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-04-postgres.py`：**85检查通过**，最新 `logs/stock-completion-b04-pg-20261019-104809.json`；隔离schema清理true，公共业务写/上游/付费模型调用0。4份新报告走真实create→claim→finish→release，1份明确旧方法夹具；不把合成保存结果当真实行情认证。
5. 根目录 `./scripts/run-local.ps1 -TimeoutSeconds 35`：退出0，五服务启动。`logs/stock-completion-b04-run-local.log`。
6. 实际PG只读启动校验与缺表/缺列拒绝：`logs/stock-completion-b04-schema-pg.log`，39表496列、前后均通过；无DDL/数据更新。
7. 隔离前端构建：复制源码到 `logs/b04-build-workspace`，不复制.env/.next，junction复用node_modules。`node logs/b04-build-workspace/node_modules/next/dist/bin/next build <绝对隔离目录>`；最终编译/类型/40页静态生成退出0，`logs/stock-completion-b04-build-final.log`，但standalone复制node_modules出现Windows EPERM警告，**不能把退出0当可发布standalone包**。用户中途消息打断的一轮只编译到类型检查，未当成功。未碰正在运行的frontend/.next。
8. 实际HTTP：前端/login200、后端/health200、桥/health200且Redis=true；Next研究availability匿名401；OpenAPI新export/compare路由均已注册。market.ok/live.ok/virtual.ok=true只是进程缓存可用，不认证期货数据来源/新鲜度。
9. 最后复核 `logs/stock-completion-b04-runtime-health.json`：6项HTTP/路由检查全通过、5个目标端口监听，保存UTC采样时间；不是仅引用启动器旧日志。

### 实际浏览器操作

- 新源码尚未后端部署时验证历史页明确“尚未部署报告导出与对照”，不露死入口；已保存v3报告可打开；恢复入口加载完整历史参数，明确Apply后日期为2026-07-13—2026-09-11，未点击新建/交易。
- 最新实例启动后：浏览器历史页勾选两份实际已保存v3组合报告，读取对照成功，原收益-5.10%和0.00个百分点差值正确；各自曲线/原版本/警告可见，没有模型/新研究。
- 实际CSV与JSON按钮触发浏览器download事件，文件位于本机下载目录。独立读取文件检查CSV BOM=true、12837字节、CSV与JSON均20条已保存交易、方法均保持原v3、数据清单存在。job.params含日期及预处理，旧result.params是另一摘要契约，二者不是整对象相等；共有字段一致，未用当前默认值改造旧结果。`logs/stock-completion-b04-browser-download.log`保留该诊断。
- 未操作真实或模拟下单/资金、没有新建研究任务；没有本批全站浏览器/所有期货页面验收。临时验收页不取代用户原交易页。

## 兼容、金融口径、成本及回退

- 金融公式/行情/复权/账本未改；本批报告仅读原保存数据，恢复使用当前方法另行新建。未知PIT、当前分类幸存者偏差、缺基准/五年资源等仍未认证。
- 无数据库迁移；新availability字段向后兼容，缺字段前端关闭新报告操作。撤回报告router/隐藏入口即可停止新只读能力，已有报告与参数不受影响。
- **启动安全门不得随代码回滚重新启用旧自动DDL。** 如表/列不兼容，保留数据、停服务、评审版本化前向修复。P01的全部旧DDL拆迁、类型/约束兼容与新库初始化仍未完成。
- 报告、恢复、比较、隔离验证没有上游/付费模型调用；项目完整启动会恢复原定时行情/新闻/任务worker，已有任务的常规外部活动不在本批零调用统计内，不能谎称整个运行实例永远零成本。本次没有主动创建/调用付费模型任务。
- 仍无生产部署、完整回滚演练或用户全站人工签收；Windows standalone打包警告仍是P03风险。不要停止续做自动化。

## 人工验证与下一轮四包

1. 打开 `http://127.0.0.1:3000`，登录A股，历史报告详情下载JSON/CSV；核对原方法/参数/明细，缺结果应明确失败。
2. 勾选两份同类型完成报告，对照；不同版本/区间只有并列值，差值不得出现；切用户/登录盘验证404。
3. 历史配置恢复 → 检查 → 明确Apply → 覆盖预检 → 另行新建。已有运行/未知提交时应先核对，不能覆盖或重复建任务。
4. 停启本地服务使用现有受控启动器；缺失schema不自动修库，健康200不代表行情已获认证。

下一轮：①S04持久用户确认及同事务写幂等；②P01逐项版本化迁移、约束/新库/前向修复和发布演练；③R02旧研究长HTTP入口归一；④D01盘后覆盖/缺口/恢复状态。R06/R07本批只交付保存结果操作，不标基准或同版本重算完成。

## 最终检查点（北京时间18:55）

后端2452通过/3既有skip，前端406通过，TSC退出0，隔离PG85通过且清理；最新五服务正常监听并真实HTTP200，报告对照和两个文件下载已浏览器验证。启动门只读检查替代自动DDL，无生产迁移。仍部分完成；独立后端复核、完整P01/P03、全站验收和数据覆盖等风险保留，不停止自动化、不声称系统正式上线。
