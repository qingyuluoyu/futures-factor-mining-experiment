# B18 单日盘后更新真实检查点

2026-10-21 01:08，Asia/Shanghai。B18于20日开始、21日凌晨续做。状态：四包实现与验证完成，并完成**2026-10-20**的有界真实盘后更新；不是21日未收盘数据。系统仍部分完成，未发布/浏览器/人工验收，不是全市场或全部历史认证。

## 四个工作包与实际文件

1. 核实20日无任务、账号仅显示configured布尔，B17保护974文件开工verify通过。新`scripts/check-stock-b18-preservation.py`、`backend/tests/test_stock_b18_preservation.py`仅允许新增本地股票操作模块和CLI；旧股票worker/API/frontend/期货/共享生产逻辑冻结。
2. 新`backend/scripts/stock_eod_operator.py`、`scripts/sync-stock-eod-once.py`及`backend/tests/test_stock_eod_operator.py`：本机PG5433、上海最近7个已到16:30窗口的日期、操作者和新logs JSON校验；先独占回执再DB/schema check。派生Teajoin来源仅顺序trade_cal/stock_basic/daily，最多3、单请求20秒/8MiB、单轮有限时、无重试。收到响应后规范化及B17语义校验，再复用原run_eod持久claim/事务；不改原worker或恢复规则。
3. 新`scripts/test-stock-completion-batch-18-postgres.py`，原B02测试harness仅新增b18报告标签；真实私有PG/JWT/HTTP与源边界MockTransport、自查/独立复查和完整回归。新日提交、重复0源、429停止、取消、旧快照/旧价量/期货fixture不变均验证。
4. 真实只读状态指纹和目录备份→仅20日受控更新→只读复核及对账→检查点。真实写入新20日EOD批次与规范股票目录，不能把本批写入说成public业务写0。

## 实现和金融/安全口径

- 复用原按日数据库锁/唯一领取；已有任何任务日期均不自动重复来源，失败需独立审计恢复。文件回执只防误重复操作，不替代DB幂等。不是因子/回测业务使用次数门槛。
- 回执在每次可能收费请求前落盘/fsync，记录允许的API名、响应hash/字节/行数/时点，不存token、响应正文或供应商异常文本。reserved_attempts是保守预算计数而非计费证明；本次三个成功响应证明实际完成3个HTTP请求。
- 本人发现原地truncate可能损坏前次回执，补真实失败反例后改为独占初建、同目录临时JSON fsync+原子replace；替换失败保留上次有效JSON并停止外部调用。Windows不持有目标文件打开句柄。文件系统断电持久性不冒充DB金融账本保证。
- DB可能已提交、随后回执写失败：CLI明确要求核对数据库和回执，不能说已回滚或盲重试；回执observed-result用于区分此路径。不将文件和DB声称为跨资源原子事务。
- 原价/手单位/金额转换沿既有版本；新适配器先通过B17字段/哈希/覆盖/价量校验，旧qfq不重导、不用旧价填当前缺价，不把unknown自动升级。模型调用0，不接真实资金或券商。

## 实际命令

Python在仓库，Node/TSC在frontend执行；日志在logs，无依赖升级/提交/分支。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b17-preservation.py verify logs/futures-preservation-b17.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b18-preservation.py snapshot logs/futures-preservation-b18.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_eod_operator.py backend/tests/test_stock_b18_preservation.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-18-postgres.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 scripts/sync-stock-eod-once.py --help
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b18-preservation.py verify logs/futures-preservation-b18.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b18-preservation.py snapshot logs/futures-preservation-b18-expanded.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b18-preservation.py verify logs/futures-preservation-b18-expanded.json
backend/.venv/Scripts/python.exe -X utf8 -c $program --output logs/b18-state-before.json --backup logs/b18-catalog-before.json
backend/.venv/Scripts/python.exe -X utf8 scripts/sync-stock-eod-once.py --date 2026-10-20 --output logs/b18-live-eod-20261020.json --operator-ref local-authorized-b18
backend/.venv/Scripts/python.exe -X utf8 -c $program --output logs/b18-state-after.json
```

内联审计$program只允许本机5433、60秒总时限/SQL8秒/锁2秒，REPEATABLE READ+READ ONLY并核实on；查询下述保护表count及按id排序行内容摘要、旧日线count/maxid/maxdate和首尾20行摘要、除20日外旧EOD摘要、目录≤10000行备份、目标日元数据和B17缺口服务。前后JSON及比较产物保存，不打印账号或配置内容。

## 红绿、通过和未执行结果

- 初次模块缺失5失败在`b18-operator-red.log`，不是金融断言红。初始目标6通过后，本人原子回执反例`b18-atomic-red.log`1失败5通过→修复。独立复查配置保护漏项反例`b18-config-red.log`1失败1通过→修复，最终`b18-target-final.log` **8通过**。
- 真实私有PG/JWT/ASGI **54检查通过且schema清理成功**：`stock-completion-b18-pg-20261020-170022.json`、`b18-pg-reviewed.log`。真实事务/幂等/失效/取消/HTTP权限，源边界仅MockTransport，不是真实付费接口认证。目录测试克隆表显式改用私有sequence，避免消耗public序列。该隔离报告0真实来源/模型/public业务写，与下面实际操作明确区分。
- 最终完整后端 **2654通过、3既有skip、736warnings，125.92秒，退出0**：`b18-backend-confirmation.log`。前一次2653结果在新增配置保护测试前，未当最终数量。没有新增skip/删测试/消除无关warning改期货；原通知AsyncMock未await等仍在。
- 完整Node **496通过、0失败/跳过**，TSC退出0：`b18-node-full.log`、`b18-tsc-full.log`；CLI --help退出0。前端生产代码本批未改。
- 本轮没有构建、部署、服务启停或浏览器/人工验收，未宣称运行中的完整项目已可用。B13 EPERM和专用服务恢复仍是待办。

## 真实盘后更新结果与副作用

- 前置`b18-preflight-state.json`/`b18-state-before.json`：20日不存在，最后有效19日，目录5558，13缺/19unknown；没有重试已有失败日或覆盖旧有效日。
- 实际`b18-live-eod-20261020.json`、`b18-live-operation.log`：**3次成功来源响应**，顺序trade_cal/stock_basic/daily，日历确认开市，完整分页false；收到daily5568行、目录5558、其中有价5549、缺9、unknown19。完成状态诚实为partially_completed，不是全覆盖。
- 数据版本`087651ac5cead93b4d3be4e826fc21ae64334e2f0a9f942252cbc3c7d33d3580`，job_id `f169cfa6-caeb-4965-ba15-e79980c267b5`。20日新增批次正常单次attempt=1；规范目录5558条upsert，当前条数未增，updated_at/来源字段会更新，目录sequence可能按原upsert语义前进。无生产DDL/新迁移/删除/旧历史重导。
- `b18-state-after.json`/`b18-state-comparison.json`：版本等于入库前校验回执，B17实际读取最新日已20日、缺9/unknown19；19日等其他EOD批次完整行摘要不变。旧stock/futures日线count/maxid/maxdate及首尾20行摘要不变，**此项不是所有历史逐行认证**。
- paper_accounts/positions/orders/ledgers以及ai_trading_tasks/run_logs/equity_points/decisions的完整行内容摘要和数量前后相同；仅保存摘要不输出持仓/任务正文。结合代码保护，未因本轮股票操作改动期货账本/任务。
- 成本：真实来源请求3、模型0；供应商实际货币/额度扣费未读取，以账户计费为准，不能说免费或全部调用0。预算已用完，本轮未追加样本/缺口来源请求或重试。

## 复查与保护证据

- 独立审查先因额度失败；到错误提示重置时间后只重试同一审查一次取得结论，没有换代理绕过。提出P2：原inventory没涵盖实际.env/.env.runtime；只在新B18保护器补SHA256，无明文读取展示，tmp fixture验证修改/删除检测。
- 原984基线保留。`b18-config-expansion-diff.log`预期verify退出1：新增两个配置文件、仅保护器自身changed、无删除；原983其他受保护文件匹配。新增两配置只从此时起保护，不追认开工时状态。
- 扩展`futures-preservation-b18-expanded.json` **986文件**，`b18-preservation-final.log`verify通过，added/removed/changed均空；实际调用前亦verify。独立只读复核P2已修，无新增具体P1/P2，未读配置明文/运行测试/访问网络或DB。

## 回退、风险、人工和下一四包

- 本批确有授权public股票写入，不能泛称只读。先有`b18-catalog-before.json`备份和前后审计；旧有效EOD仍保留，未直接删除或反向重导。股票目录恢复必须核对预期后值且无并发新写，不盲覆盖；默认独立审计前向修复。数据回退不是只回退代码，不能虚称一键无损回滚。
- 入口代码可独立撤回本地CLI/新模块，旧worker/API契约完全保留；不回退前批金融/权限修复。没有把本CLI暴露成Agent高权限工具或网页管理员接口。
- 仍有9缺/19unknown原因待核验、旧历史/PIT/锚点/五年、DSL/基准、S04/S05、股票AI、GP/真正期货研究、服务/构建/人工发布验收等，不因20日更新自动完成。shadow归档/回放仍需独立验证，不将新原价混入旧qfq。
- 人工未执行：恢复专用项目→登录A股→板块/覆盖/缺口核20日、5549/5558及9/19→查来源版本/量单位→确认旧研究/回放仍清楚限制→切期货核资金/订单/任务不变。实际统计随后续数据变化，不能硬编码这些数字。
- 下一四包：复用已保存EOD证据的原价归档/兼容版本读取（0新增来源优先）；剩余9/19原因和历史缺口/授权/锚点预算；可信版本股票DSL/基准/研究垂直切片；独立专用服务/构建/发布回退/双市场人工验收。1:00/6:10等续做保留，本轮不满足整体停止条件。
