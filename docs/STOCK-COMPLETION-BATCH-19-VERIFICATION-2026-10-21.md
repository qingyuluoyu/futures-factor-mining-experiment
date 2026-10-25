# B19 固定版本原价读取与20日影子归档真实记录

## 状态与边界

2026-10-21 02:22，北京时间。B19四包已实现并通过本机验证，实际归档20日已存快照并成功内部导出；**全系统部分完成，未发布**。没有新增网页或切换K线/研究/回放读路径，不把内部审计工具称为用户研究功能完成。

开工读用户AGENTS、Oct17扩展计划、Oct19主计划、Oct20期货保护计划和B18真实记录，核实没有正在实施的其他执行者。原B18扩展986文件verify通过；本轮新989基线最终零漂移，并逐项再次核对B18原986文件未变。未修改既有期货/共享/股票生产代码、运行配置、依赖和迁移文件，不提交或建分支。

## 文件及数据流

- 新`backend/app/services/stock_raw_panel.py`：显式1—10个SHA256归档版本和1—10个带交易所标准证券标识，拒绝裸代码/重复/越界。三次有界查询批量读归档元数据、对应原EOD、全量观察，最多100000行；不自动改读latest。复用旧prepare_archive重新校验来源hash、单位/标识/采集及归档时间、覆盖分区/原价，再核验全量影子行，最后才按证券过滤。缺源/篡改/重复日期失败，不返回空成功。
- 新`scripts/export-stock-raw-panel.py`：本地操作员入口，只允许localhost/127.0.0.1:5433；REPEATABLE READ/READ ONLY并核实on、45秒总时限/SQL8秒/锁2秒；配置可由旧加载器读入但不输出。仅项目logs直接新JSON；同目录临时文件写入fsync/关闭后硬链接独占发布，禁止覆盖，清理临时文件。不保证断电目录落盘，不宣称备份设施。
- 新`scripts/check-stock-b19-preservation.py`：仅允许上述两个新文件，继承保护旧操作器、worker、旧股票/期货、前端、迁移/运维和配置hash；测试证明旧B18入口也被冻结。
- 新测试`backend/tests/test_stock_raw_panel.py`、`test_stock_raw_panel_cli.py`、`test_stock_b19_preservation.py`；新`scripts/test-stock-completion-batch-19-postgres.py`；原测试harness02仅允许b19报告标签，不改变任何生产接口。
- 新子计划`docs/superpowers/plans/2026-10-21-stock-completion-batch-19.md`；主计划和期货保护计划添加本检查点。原API/前端不改，没有半链路暴露。

## 金融和版本契约

产物`stock-raw-panel-v1`含固定归档版本、来源版本/批次、方法、交易日、采集及归档时刻、完整行数/缺/unknown、金融元数据和内容SHA256。数字输出为十进制字符串，使用字符串去冗余零而非Decimal.normalize，避免默认28位精度舍入，也不经过float；0.01手和38位边界已覆盖。每个选定证券/日期要么观测要么显式source_missing/source_unknown_quarantined/outside_snapshot，后者只表示不在快照，并非停牌/退市原因。

价格未复权none、成交量lot_100_shares、金额CNY，原始JSON数值转Decimal的旧证据局限保留；没有宣称取回原始响应数值文本或完整上游真实性。**仅核验同源存储内容一致**，不是独立数据认证。data_available_at=null、point_in_time_verified=false、eligible_for_backtest=false、calendar_continuity_verified=false；不补因子/锚点/公告时间/缺价，不混旧qfq，不授予新回放资格。产物哈希不含生成时刻，绑定选择条件和所有输出内容，乱序请求结果一致。

## 实际命令与证据

在仓库执行Python，Node/TSC在frontend。所有日志在logs，无升级/服务启停。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b18-preservation.py verify logs/futures-preservation-b18-expanded.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b19-preservation.py snapshot logs/futures-preservation-b19.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_raw_panel.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_raw_panel_cli.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_raw_panel.py backend/tests/test_stock_raw_panel_cli.py backend/tests/test_stock_b19_preservation.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-19-postgres.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 scripts/export-stock-raw-panel.py --help
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b19-preservation.py verify logs/futures-preservation-b19.json
```

实际操作命令（同batch/hash先dry-run，后apply，最后再apply做幂等全量比对）：

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/archive-stock-eod.py --batch-id f169cfa6-caeb-4965-ba15-e79980c267b5 --expected-data-version 087651ac5cead93b4d3be4e826fc21ae64334e2f0a9f942252cbc3c7d33d3580
backend/.venv/Scripts/python.exe -X utf8 scripts/archive-stock-eod.py --batch-id f169cfa6-caeb-4965-ba15-e79980c267b5 --expected-data-version 087651ac5cead93b4d3be4e826fc21ae64334e2f0a9f942252cbc3c7d33d3580 --apply
backend/.venv/Scripts/python.exe -X utf8 scripts/export-stock-raw-panel.py --version f372ea185838fcad514e95224a7d23e23a08b5aa4f39561e12b81cb8cdec6e1f --version c79735c1a63888292bfffe93646832042ca75ea6034d1809ac70846f203ca31a --version edc0940e76d6fbee83581387e756b14004a685a699c089c7ff93bca2f21b8950 --instrument 600519.SH --instrument 000001.SZ --instrument 601059.SH --instrument 920229.BJ --output logs/b19-live-raw-panel.json
backend/.venv/Scripts/python.exe -X utf8 scripts/export-stock-raw-panel.py --version edc0940e76d6fbee83581387e756b14004a685a699c089c7ff93bca2f21b8950 --instrument 600519.SH --output logs/b19-live-raw-panel.json
$program=Get-Content logs/b19-audit-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b19-state-final.json
```

最后重复output命令**预期退出1 FileExistsError**，旧产物不覆盖、在DB连接前拒绝；不是新增功能故障。初次before/after审计和比较使用内联`python -c $program`，完整实际程序分别保存`logs/b19-audit-command.txt`、`logs/b19-compare-command.txt`便于复核；输出文件必须是新文件，不能直接重跑覆盖证据。审计限定本机5433、45秒/SQL8秒/锁2秒、READ ONLY+REPEATABLE READ，输出资源摘要而非账号/持仓明文；比较13项通过。

## 红绿、回归、独立复查

- `b19-reader-red.log`20失败为新模块缺失；`b19-cli-red.log`6失败为入口缺失，**不是旧金融行为断言红**。随后实现，新增精度与保护测试后`b19-target-final.log` **28通过**；未删测试/放宽断言/新增skip。
- 完整后端`b19-backend-full.log` **2682通过、3既有skip、735warnings，136.95秒，退出0**。既有通知AsyncMock未await等仍保留，未借消warning改期货。
- 完整Node`b19-node-full.log` **496通过、0失败/skip**，TSC退出0，help退出0。
- 真实私有PG/JWT/ASGI **58检查通过，schema清理成功**：`stock-completion-b19-pg-20261020-181930.json`、`b19-pg-final.log`。真实Decimal/全量内容、只读事务实际写入25006拒绝、缺版本/缺source/源篡改/未选中行篡改、CLI真实只读helper、旧HTTP401/期货409/股票disabled+trace+no-store、私有账本正常读写隔离回归；外部模型为既有harness边界fixture，非模型认证，0真实上游/产品模型/public业务写。先前56项中间结果仅保留参考。
- 989保护preapply/final均通过，added/removed/changed为空；原B18的986项另逐项一致。新增B19保护器是新增受保护文件，不追认未知历史。
- 一次独立只读审查未发现明确P1/P2；审查者静态阅读服务/CLI/guard/单测/PG及已有日志，不访问网络/DB或.env明文，不替代主执行者真实运行。审查时读到26目标日志，最终新增2测试及58PG结果由本执行者核实，不能假称其独立执行全部回归。

## 真实归档、导出及副作用

- `b19-state-before.json`确认只有16/19日归档11093行，20日来源已存且id/hash匹配B18，20日观察0；未重新抓取20日，更没有抓21日未收盘价。
- `b19-archive-dry-run.log`、`b19-archive-apply.log`、`b19-archive-repeat.log`：新增20日批次`42fdc06e-0abe-4552-9fce-e37c7d98f23c`、版本`edc0940e76d6fbee83581387e756b14004a685a699c089c7ff93bca2f21b8950`、**5549条原价观察**；仍9缺/19unknown，不伪称全市场覆盖。再次apply为already_archived，全量内容和元数据一致，不新增/覆盖。
- 三日影子合计**16642条**。`b19-export-live.log`、`b19-live-raw-panel.json`实际按3个固定版本核对全部16642行，再导出4证券×3日：600519/000001共六条观察，**601059三条缺、920229三条unknown隔离，共六条显式缺失**；没有凑12条K线。内容hash `669708ad5ed40a44cf9dbeb68f4497eb8d93922623a19e2209ed26d314281e7d` 复算一致。原始产物为精确字符串，未把它投入交易或回测。
- `b19-state-after.json`、`b19-state-final.json`和`b19-state-comparison.json`：期货/共享paper四表、AI交易四表、股票目录/EOD/归档任务全行摘要和数量不变；候选恢复表名不存在会记exists=false，不代表实际其他恢复表已审计。旧两日归档元数据及11093观察全行摘要不变。旧stock/futures日线count/maxid/maxdate和首尾20行摘要相同，**不是全部历史逐行审计**。目标20日仅新增1影子批次/5549观察，源快照未变。
- 本轮确有上述public影子写，不能泛称public零写；私有测试的零业务写与实际操作分开。没有生产DDL，旧apply只检查既有004迁移；私有schema迁移正常应用/清理。来源及产品模型调用**0新增**，因此没有本轮新增上游/产品模型请求费用；B18已发3请求计费仍未知，不倒算免费。

## 迁移、回退、运行及人工

无新表/列/版本迁移、依赖或配置变化。新模块/CLI可独立撤回，旧用户路径及旧文件原样兼容；不能把撤回代码说成已撤销影子数据。默认保留新影子证据且停止引用，有质量冲突采用新版本/前向核验，不在不确认并发引用时删除批次或改写旧来源/旧qfq。当前没有用户消费者切换，不用改期货或清其缓存。

内部手工操作路径已由以上真实CLI执行验证；用户页面人工验收、完整构建、服务恢复/重启、发布回退演练**未执行**。实际只读端口观测仍PG5433及3000/3001监听，后端8002/Redis6380/bridge8110未监听；没有抢占、杀进程或把不明页面200当项目健康，B13构建EPERM仍待独立处理。硬链接发布在当前NTFS已成功，其他文件系统若不支持应明确失败，不静默改为覆盖写。

人工复核：查看本轮三个归档日志与产物hash/单位/三日版本及缺失；确认`eligible_for_backtest=false`；后续恢复专用服务后登录A股核覆盖/限制，再切期货对账户/订单/资金/AI任务；生产发布前完成部署/迁移/回退和双市场验收，不能只看测试数量。

## 剩余风险和下一四包

1. 当前9missing/19unknown的停牌/上市目录等原因尚未来源核验；先复用证据对账，必须额外查询时明确授权/端点/预算，不能靠名称推断或无预算重试。
2. PIT/历史目录/复权锚点、旧300股历史质量及五年8931缺价/600221持仓估值缺口仍未解决；内部原价产物不是合格回测面板。建立可信版本资格和可得时间证据后才推进版本reader消费者。
3. 股票DSL/可信基准/异步研究及模拟回放扩容、受控AI/历史报告等垂直链路继续；真正期货研究/GP/S04/S05等按保护计划另案，不能为快通而删安全门。
4. 独立运行/构建/发布回退及双市场人工验收。续做自动化均保留，未满足整体完成停止条件；读取本记录与最新计划，不重做B19/重复归档或抓源。
