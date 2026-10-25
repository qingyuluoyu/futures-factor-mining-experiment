# B17 逐证券盘后缺口真实检查点

2026-10-20 23:26，Asia/Shanghai。状态：四个限定工作包已实现并通过本机代码、私有PG/HTTP及真实库只读验证；未发布、未完成浏览器/人工验收，整个系统仍部分完成。没有补全缺价或认证全市场数据。

## 四包实现及实际文件

1. B16开工968保护文件verify通过；新`scripts/check-stock-b17-preservation.py`、`backend/tests/test_stock_b17_preservation.py`收紧本批白名单，保存974文件基线，复用迁移/运维/bridge完整保护。期货/共享/worker/其他股票生产逻辑冻结。
2. 新`backend/app/schemas/stock_eod_gaps.py`、`backend/app/services/stock_eod_gaps.py`、`backend/tests/test_stock_eod_gaps.py`；股票`backend/app/api/stock_market.py`只加GET `/api/stock/market/eod-gaps`、专用no-store Route及导入，不改旧端点或共享Route。
3. 新`frontend/src/lib/stock-eod-gaps-api.ts`及测试、新`frontend/src/components/market/stock-eod-gap-browser.tsx`及测试；股票覆盖面板`stock-data-availability.tsx`及测试只加显式入口。新客户端捕获JWT、20秒/no-store/取消；草稿与应用、分类/关键词/分页/重置/重试和scope晚到隔离完整接通。
4. 新`scripts/test-stock-completion-batch-17-postgres.py`；B02测试harness仅加b17报告标签。逐包本人自查、独立只读复查、完整回归、真实私有PG及真实库只读检查。子计划`docs/superpowers/plans/2026-10-20-stock-completion-batch-17.md`及主/分阶段计划更新，无提交/分支/新依赖。

## 数据流与金融口径

股票身份权限→typed Query→最新成功/部分成功且非未来EOD批次→批次/快照日期、方法、hash、集合/计数、类型、OHLC及来源口径校验→当前目录注释→当页股票历史聚合→有限分页。kind=all/missing/unknown、keyword≤64、limit1—100、offset0—20000，expected_fingerprint可选64hex；8秒，trace/no-store。无效422、匿名401、期货409、坏快照/DB/超时503，均不返回空成功。

- missing只说明快照目录缺行情，unknown说明快照时未匹配目录；不能推断停牌/退市。unknown后来入当前目录仍保留原分类，不自动纳入行情或开放交易。
- 名称/行业/上市日是当前目录注释，不是历史成分；证券ID明确来源快照/当前目录/不可得。当前目录缺失/交易所冲突不开放详情跳转；其余只读详情不是可交易认证。
- 历史按查询时旧股票库、交易日≤快照日统计，不证明当时已采集可得，不认证连续性/锚点。SQL行数0不是补造零价/量。指纹绑定快照/当前目录/查询条件，**不冻结历史统计**；旧价绝不补快照缺价。
- 翻页变更409要求重应用，客户端也拒绝200伪装的指纹变化/来源消失、分项与返回类别/总数矛盾。disabled/no_snapshot时snapshot/totals=null，不当作缺口0或全覆盖。
- 来源只返回允许字段：供应商、固定公开URI、采集时点、版本、未复权/量单位、授权限制。source_evidence_recorded只是存过元数据字典，未独立核验；hash不是交易所认证、账户配置不是转授权认证，不泄露原证据任意字段。

## 实际命令和证据

Python在仓库执行，Node/TSC在frontend目录；日志保留，不删/跳过测试或关闭类型检查。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b16-preservation.py verify logs/futures-preservation-b16-expanded.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b17-preservation.py snapshot logs/futures-preservation-b17.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_b17_preservation.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_eod_gaps.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_eod_gaps.py backend/tests/test_stock_screen.py backend/tests/test_stock_market_board_api.py backend/tests/test_stock_b17_preservation.py -q
node --import ../logs/node-alias-register.mjs --test src/lib/stock-eod-gaps-api.test.mjs src/components/market/stock-eod-gap-browser.test.mjs src/components/market/stock-data-availability.test.mjs
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-17-postgres.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b17-preservation.py verify logs/futures-preservation-b17.json
```

- 首次SQLite fixture同code/date写stock/futures触发旧唯一约束，`b17-backend-red.log`1失败/10setup errors，不算业务红测；只将futures反例改为另一早日期，不改生产约束。修fixture后`b17-backend-business-red.log`11失败，再实现新端点。前端缺模块的初始失败也不冒充金融断言。
- 本人自查真实红→绿：422无no-store、0冒充false分页标志、字符串量、负金额、Unicode代码，`b17-self-review-red.log`10失败5通过→修新service和仅新端点Route；最终目标`b17-backend-target.log`31通过/40warnings。
- 独立复查两项P2补真实反例：分项/总数与返回类别矛盾，固定指纹却接收不同指纹/来源消失200。`b17-review-counts-red.log`、`b17-review-pin-red.log`确实Missing expected rejection→修复→`b17-client-confirmation.log`14通过。另补历史“查询时不证明当时可得”披露反例`b17-history-disclosure-red.log`，失败后只补说明并重跑完整前端。
- **完整后端2646通过、3既有skip、734warnings，151.57秒，退出0**：`b17-backend-full.log`。保留原AsyncMock未await等warnings，未顺便改期货/共享代码。
- **最终完整Node496通过、0失败/跳过，TSC退出0**：`b17-node-final.log`、`b17-tsc-final.log`。不是最后说明文本修改前的中间回归。
- **真实专用PG5433/JWT/ASGI HTTP60检查通过、私有schema清理成功**：`stock-completion-b17-pg-20261020-152024.json`、`b17-pg-first.log`。真实SQL验证未来/失败批次排除、股票/日期隔离、分页并集、unknown不升级、当前目录变动409、allowlist、非法参数和hash坏503；public三数据表行数不变，原harness三个期货fixture账户余额/订单/流水不变。
- 私有证券/行情/批次和模型工厂均为明示fixture，不是源认证/真实模型调用或撮合资格认证。测试不是已运行服务器的socket E2E。

## 真实库只读对账

内联Python先stdin、后`backend/.venv/Scripts/python.exe -X utf8 -c $program`加载既有配置且不打印秘密；只允许本机PG5433，15秒/SQL5秒/锁2秒，REPEATABLE READ + `SET TRANSACTION READ ONLY`，确认`SHOW transaction_read_only=on`，调用`read_eod_gaps(session, EodGapQuery(limit=100), enabled=True, today=上海今天)`。控制台表格首次未展示字段，后用JSON摘要核实两份原始JSON均有效且一致，不误报为空审计失败。文件`b17-live-gap-readonly.json`、`b17-live-gap-readonly-v2.json`。

- 23:22最新可读快照仍**2026-10-19**，13缺行情+19未匹配目录=32条；全部返回。19条当前目录仍缺失，32条中31条在旧股票历史库按交易日≤19日无记录。
- 仅601059这条缺行情有旧历史861行、最后2026-09-14；不能拿旧价补19日收盘，不推断停牌/退市。没有抓20日收盘、核实来源、补价/量或改目录/快照。
- 两次读取均0来源、0模型、0public业务写；这只是本地记录事实，不认证同源样本或全部历史/金融正确性。五年缺价、PIT/版本、DSL/基准、S04/S05、股票AI、GP/真正期货研究等未解决。

## 自查、保护、回退和成本

- 最终`b17-preservation-final.log` **974文件passed，added/removed/changed均空**；保留开工基线，不重建掩盖漂移。B17计划允许股票文件有改动，不能说旧B16清单在新股票改动后仍全部零漂移。
- 本人逐包检查旧调用方、异常、分页、金融/权限和时点；独立只读复核P2修复后无遗留具体P1/P2。审查者未写文件/访问网络或DB/启动服务/自行执行测试。之后仅增加一处历史时点披露，完整前端验证覆盖。
- 无生产DDL/迁移/回填、依赖、来源/付费模型调用、期货/共享/worker/配置修改；私有建表/清理不应用于public。不启动、停止或抢占服务。成本仅有界本地读取，不恢复业务次数门槛。
- 未做生产构建/部署/浏览器/人工验收，B13 EPERM和服务恢复仍待独立包，不能称已上线。新接口增量兼容；旧后端404在新前端明确失败，不假装零缺口。
- 回退仅撤新股票入口/端点/模块，无数据逆向迁移；不回退前批金融/权限修复。保护漂移先核实，不覆盖用户修改。

## 人工路径与下一四包

人工未执行：安全恢复专用项目→登录切A股→筛选/数据覆盖→逐证券缺口→核实际日期来源数量→分类/代码/当前名称应用→翻页/重置/重试→目录或源变化要求重应用→登出/换号/切盘/快速换市场无旧响应→只读详情→切期货检查原路径。不得硬编码13/19为验收结果。

下一四包：真实缺口原因与授权Teajoin有界源预算；历史缺价/PIT/授权/锚点和兼容版本读；可信版本上的股票DSL/基准及研究迁移；专用服务/构建/发布回退/双市场人工验收独立包。共享/期货遗留另包兼容评审，不夹带。本对话1:00/6:10等既有续做保留，不重复创建；全系统未达停止条件。
