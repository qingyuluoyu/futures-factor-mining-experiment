# B16 股票覆盖资格分层真实交付

记录时间：2026-10-20 22:56，Asia/Shanghai。状态：四包代码及目标/完整后端/完整前端/类型/私有PG验证通过；尚未发布、浏览器和人工验收未执行，整个系统仍部分完成。

## 四个工作包和实现边界

1. 核实既有API/数据模型/测试/所有复用调用方，冻结期货、共享和后台生产源码；B15保护清单先verify通过。新B16清单收紧为本批股票前端白名单，B15股票后端及筛选客户端同样冻结。
2. 新股票覆盖聚合客户端复用四个既有GET：boards、research/coverage、research/availability、replay/availability。同一捕获JWT、no-store、约20秒超时、用户取消、不重试；逐来源成功/失败分开，数据库失败不冒充零覆盖。不修改后端生产接口或旧前端API方法行为，仅股票API模块增加Schema导出。
3. 筛选页显式“查看数据覆盖资格”，开启前不请求四来源。日期草稿→合法性/上海今天/顺序/3660天上限→显式应用；重置恢复服务端推荐窗口，不默认抓取或计算五年。账户/市场/JWT盘/登录代次变化取消清空并防迟到，切市场同key往返也失效。数据显示20个回放数据集/次，可逐步显示至接口100上限；无自动创建/下单/补采。
4. 真实私有PG+JWT+ASGI四API链路、完整受影响回归、保护器反例/独立只读复查、检查点和两个新续做时间。

## 金融口径与契约

- 目录数、当前行情有价/可比数量及日期来源单独显示；EOD提供缺行情/未匹配目录代码时只展示前30条并注明截断，不把未知证券当已授权可交易证券。
- 历史只统计实际本地观测数/区间，不证明连续交易日、价量/授权/历史时点认证；前复权未版本化研究历史不用于未复权模拟撮合。
- 研究显示实际预检区间、证券数/观测日/价量行数/缺价格和原warnings。within_limits只说明资源上限内，不代表资格/策略通过；worker不能接单与无历史不同。此面板使用既有legacy覆盖计数；具体因子依赖仍去研究页验证，不与v2口径混称。
- 回放仅展示已返回的未复权数据集版本、来源、区间；最多100数据集、可能同证券多版本，既不称总数也不称全市场可交易。enabled=false明确是功能/迁移未就绪，不说零注册；真正创建/推进仍走既有严格回放服务。
- 四来源独立读取，面板明确不是同一不可变数据版本。未修改金融公式、费用、账户、资金、订单/任务状态或数据库结构。错误信息固定化，HTTP失败可显示脱敏request_id，不输出供应商密钥或原始DB细节。
- 新客户端派生校验拒绝quoted>catalog/comparable>quoted、disabled却有数据集、反向注册区间、空版本和重复dataset id；只影响新增面板，不放宽或改写旧API契约。

## 实际文件

- 新`frontend/src/lib/stock-data-availability-api.ts`及`.test.mjs`；新`frontend/src/components/market/stock-data-availability.tsx`及`.test.mjs`。
- 改股票专用`frontend/src/components/market/stock-screener-workspace.tsx`及测试加显式入口；`frontend/src/lib/stock-market-api.ts`、`frontend/src/lib/stock-replay-api.ts`各只增加一个既有Schema导出。
- 新`scripts/check-stock-b16-preservation.py`、`backend/tests/test_stock_b16_preservation.py`，基础B15保护器未改。
- 新`scripts/test-stock-completion-batch-16-postgres.py`；旧B02测试harness仅加b16报告标签，非后台生产修改。
- 新子计划`docs/superpowers/plans/2026-10-20-stock-completion-batch-16.md`、本交付，并更新主/分阶段计划。B15交付目标命令曾误写不存在test_stock_market.py，本轮按原日志纠正test_stock_market_board_api.py并保留更正说明，不更改原日志。

## 实际命令与证据

仓库运行Python命令，frontend目录运行Node/TSC；日志均在logs。没有删除测试、关闭类型检查或额外引入skip。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-futures-preservation.py verify logs/futures-preservation-b15-expanded.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b16-preservation.py snapshot logs/futures-preservation-b16.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_b16_preservation.py -q
node --import ../logs/node-alias-register.mjs --test src/lib/stock-data-availability-api.test.mjs
node --import ../logs/node-alias-register.mjs --test src/lib/stock-data-availability-api.test.mjs src/components/market/stock-data-availability.test.mjs src/components/market/stock-screener-workspace.test.mjs
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-16-postgres.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b16-preservation.py verify logs/futures-preservation-b16.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b16-preservation.py snapshot logs/futures-preservation-b16-expanded.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b16-preservation.py verify logs/futures-preservation-b16-expanded.json
```

- 初始API/UI缺新模块的运行失败保留在`b16-api-red.log`/`b16-ui-red.log`，这是接线失败，不冒充金融行为断言。UI初实现1失败为测试树未展开无hook纯子组件，修正测试渲染器后14通过/TSC0，没有放宽原错误断言。
- 本人复查新增真实行为反例：不可能覆盖数字应failed但实际ready，`b16-contract-red.log`失败；新增派生校验后`b16-client-reviewed.log` **15通过**。包括日期不发无效请求、同凭据四GET、独立失败脱敏、取消、显式区间、缺口/版本/上限披露、晚到和scope往返。
- 最终完整前端 **486通过、0失败/跳过**；TSC退出0：`b16-node-confirmation.log`、`b16-tsc-confirmation.log`。先前一次485通过在增加契约反例前，不作为最终数量。
- 初次PG fixture未写schema_versions必填checksum，真实约束23502失败，`stock-completion-b16-pg-20261020-144855.json`记录且私有schema清理成功；仅补测试fixture的实际迁移文件hash，没有改生产约束或应用迁移。
- 最终真实PG **61检查通过且清理成功**：`stock-completion-b16-pg-20261020-144934.json`和`b16-pg-final.log`。四个原端点真实SQL、匿名401/期货409、未来及期货行不入股票历史、缺价1格保留、实际101注册仅返回100、worker离线不伪装无历史、禁用态和非法日期422；继承harness三个期货fixture账户余额/订单/账本不变，public四数据表行数前后相同。
- 私有证券、价量和101个空bars注册为明示合成**只读列表测试fixture**，不验证这些数据集可撮合；模型工厂同样为边界fixture，不是真实供应商认证。public业务写0、真实模型/数据源调用0。

## 保护审查及范围扩展

- B15扩展902清单本轮开工先通过；B16收紧白名单首次909文件。新增8个允许文件只涉及股票前端，不允许原B15后端/API生产修改。
- 独立审查发现P2：遗漏生产迁移、backend/scripts、bridge部署文件；不代表发现这些文件已被改动。增加真实反例`b16-guard-review-red.log`1失败1通过→扩展包装器→green2通过。未改基础B15保护器。
- 保留909清单；扩展后对旧清单verify预期退出1：`b16-guard-expansion-diff.log`，59新增、0删除，仅B16保护脚本自身changed，原908个其他保护文件无漂移。
- 扩展清单`futures-preservation-b16-expanded.json` **968文件**，`b16-preservation-confirmation.log`验证passed、added/removed/changed均空。新增59文件只有扩展时起的证据，不能追认本轮开始时状态。未声称本工具覆盖密钥配置内容或所有未知资产。
- 独立只读复核确认P2已修、无新增具体P1/P2；独立重算968个SHA256全部匹配。未写文件/访问网络或DB/启动服务/执行模型或独立重跑测试；不是全系统认证。

## 迁移、回退、成本与运行

- 无DDL、生产迁移/回填、新依赖、导入或期货/共享生产修改，不提交/建分支。后端契约原样兼容。回退仅撤回新增股票覆盖入口/面板/聚合客户端和Schema导出，无数据库逆向迁移；不回退前批安全或金融修复。
- 新增成本为用户显式展开或查询时最多4个本地只读GET，不自动轮询、不调用供应商/模型，不恢复任何业务次数门槛。已保留有限SQL/HTTP超时和有界结果。
- 22:51只读端口检查：5433 PID34720、3000 PID16348、3001 PID32392监听，8002/6380/8110未监听；未核验3001用途或抢占/停止任一进程。不能把监听当健康或业务验收。
- 本轮未执行生产构建/部署/浏览器/人工发布回退；B13构建EPERM和服务恢复仍待独立包。没有重查真实20日EOD或补采，历史/回放/缺价仍以前批证据为准，不说数据真实完整认证通过。

## 续做与人工验收

- 使用应用automation_update实际创建并view确认：a-01-00“A股系统续做 01:00”、a-06-10“A股系统续做 06:10”，本对话daily ACTIVE，时区按本机Asia/Shanghai，即首次2026-10-21。初次工具返回缺destination错误未创建，补destination=thread后成功，不重复创建。保留其他既有续做，本批不满足停止条件。
- 新提示词要求读取最新检查点、核对执行者/哈希、冻结期货、3—5包真实验证、外部阻塞先做安全部分；状态未变化无新行动保持安静。排程成功不等于保证离线机器或未运行应用能准点执行。
- 人工路径（未执行）：安全恢复项目后→登录A股→筛选页“查看数据覆盖资格”→核四范围/缺口/版本/日期→编辑区间但不提交不变→查询/重置/重试→模拟单来源失败→登出换号/切盘/快速市场往返不出现旧结果→手动进入研究/模拟核验各自校验仍生效→切期货确认原页面/交易/资金不变。
- 下一四包：股票EOD缺口逐证券详情与来源证据；全历史缺价/交易日/授权及有界补采预算清单；不可变数据版本读取与研究DSL/可信基准按依赖实施；专用服务/构建/发布回退和双市场人工验收另包。共享安全与期货遗留继续独立兼容评审，不夹带到股票UI。

## 最终确认

- 最后完整后端：**2630通过、3既有skip、734warnings，132.51秒，退出0**，`b16-backend-confirmation.log`。原完整运行2629/3skip/733warnings在增加保护器反例前，不作最终数量；没有删测试或新增skip。现存AsyncMock未await等warning未顺便修改期货/共享代码消除。
- 最后完整前端486通过/TSC退出0、PG61检查通过且清理；最终保护968文件零漂移。独立复查P2已修。以上只认证本批受测范围，不替代上线构建、实际浏览器、人工或全市场历史/金融认证。
