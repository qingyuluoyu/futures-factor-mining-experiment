# 课程整改执行01：已实施，整包部分完成

时间：2026-10-23 09:29，上海。按总包R1/D1/D2/R2四包实施离线可执行内容，不新增产品方向或重新审计全仓；用户停机门禁保持。本次有实际代码修改，但四个原包均未满足全部真实链路验收，不能勾选完成。

## 本轮实际实现

### R1 现有工作区与股票指标

修改`frontend/src/components/factor-research/factor-research-workspace.tsx`及其已有测试`factor-research-page.test.mjs`：新增显式“用当前因子配置回测”，带当前因子并保留共用日期/预处理，切回测页签但不提交任务；真正开始仍须用户再次点击。保留权重/费用/持股数需确认，不自动创建回测或改已保存报告。

修改`research-report.tsx`及测试：分组图有真实零轴，正收益向上/负收益向下，零收益不人为画最小非零柱；v5已知方法报告把win_rate标为正收益日占比、turnover标为平均调仓换手比例，明确244/旧夏普总体标准差与波动样本标准差/rf默认0。未知方法不补写v5假设，保存数值未更改；实际总明细、载入条数、页面60条和后端已截断提示区分，不能把旧导出当完整流水。

新增`backend/app/services/stock_research_v2/metrics.py`和`backend/tests/test_stock_metrics_v2.py`：纯股票独立指标，244、ddof=1统一、有效年rf转日、几何收益/波动/夏普/回撤/Calmar/正收益日占比/平均调仓换手；无回撤/零波动/单点/无换手分别null+原因。要求NAV以初始1且与收益序列一致、含建仓费用，拒绝非有限/负净值/布尔值/不一致输入。**该新方法尚未接入新v2任务/HTTP，当前v1计算和已保存结果保持原方法**，不冒称所有旧统计已被替换。

R1仍缺：新数据/方法完整任务接线、9因子真实抽样、真实PG/HTTP/本人页面验收。

### D1 数据请求及成本门禁契约

新增`backend/app/schemas/stock_provider_contract.py`和测试：9种来源类别、日期/标准证券/重复/额外字段校验；股票与指数代码规则分开，000300.SH/399300.SZ只按benchmark类型接受。预算默认0请求，必须有时区、有限非负Decimal费用/相同币种/已知本次预计费用；截止、请求/行数/费用用尽均拒绝。

`authorize_attempt`是纯预算检查，不是并发原子费用预留；**尚无出网适配器/费用ledger/全页响应校验**，真实调用前必须D1后续并发预留、实际响应行数/费用和分页契约闭合。没有根据预算Schema就发源请求，没有接触/输出密钥。

### D2 不可变JSON产物核心

新增`backend/app/services/stock_research_v2/artifacts.py`及测试：规范化JSON、SHA寻址、大小上限、临时文件/fsync/同目录硬链接原子发布、同内容并发幂等、读取重新hash/规范化校验，保留Decimal字符串和0.01等原值。不接受用户路径、非有限JSON、重复JSON键、坏hash/缺产物/越界或符号链接；测试只在pytest私有临时目录写明确QA数据，未写行情库。

此为内部存储primitive，**尚未建立DB登记、ArtifactRef权限、来源/版本表或迁移、Parquet/恢复任务**，不能直接开放读取接口或宣称已完成不可变研究数据集。部署文件系统须实测硬链接支持；不支持即明确失败，无静默非原子降级。

### R2 安全表达式编译和实际计算

新增`backend/app/services/stock_research_v2/expressions.py`、`__init__.py`及`backend/tests/test_stock_factor_expressions.py`。已有真正AST解释计算，不用eval/exec：算术、abs/log/clip/rank/zscore/正向lag/rolling_mean/rolling_std，稳定方法hash/字段依赖/合计lookback。4096字符/128节点/16层/16字段/合计244历史窗口及200万面板格上限；禁止导入、属性、下标、比较、幂、未来lag、超大窗口、未知字段、非有限常量/布尔金融输入。要求唯一有序日期和一致证券轴，除0/非法log/无穷结果保留缺失，不填0。真实Pandas面板验证滚动/排名/标准化及未来后缀不影响历史。

**尚未实现定义版本CRUD/017迁移/本人资源API/编辑器/FactorRef接入IC回测**，内部模块不挂未完成公开入口，不能称“自定义因子已可在产品使用”。

## 实际验证和失败记录

Python均为`backend/.venv/Scripts/python.exe -X utf8`，根目录运行；Node命令在frontend。

- 前端新增行为先红：`node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/factor-research/research-report.test.mjs src/components/factor-research/factor-research-page.test.mjs`，缺转移/零轴/统计说明失败，`stock-course-execution-01-ui-red.log`；实现后34目标通过。
- 后端初始缺模块collection失败为基础红证据，不充当金融行为红；具体边界反例另有5真实失败：巨型整数OverflowError、布尔面板、指数标识误拒、JSON 1e309/重复键读取误放行。`stock-course-execution-01-boundary-red.log`；修复后全部通过。metrics独立红`stock-course-execution-01-metrics-red.log`，不能把缺模块当计算已验证。
- `python -m pytest backend/tests/test_stock_factor_expressions.py backend/tests/test_stock_provider_contract.py backend/tests/test_stock_research_artifact_store.py backend/tests/test_stock_metrics_v2.py -q`：39通过，`stock-course-execution-01-backend-final.log`。
- 加跑原stock_research_engine、regressions、dependencies、snapshots、report_artifacts和ai_trading_mode_isolation、market_history_isolation共155通过/116既有warnings，`stock-course-execution-01-regression.log`。未改这些原测试，不模拟真实上游真实性。
- 前端全量：`$files=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName); node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files`。首次新增测试跨VM数组prototype使deepStrictEqual失败（值相同），使用Array.from归一测试realm后同值严格断言保留；最终658通过/0失败跳过，`stock-course-execution-01-frontend-final.log`。失败日志保留，不修改生产绕过断言。
- `node node_modules/typescript/bin/tsc --noEmit`：退出0，`stock-course-execution-01-tsc.log`。
- 未执行：真实隔离PG、新迁移apply/check、原HTTP、本人浏览器、完整生产build/发布和故障回退。用户不允重启，不能用离线结果取代这些证据；本轮无DDL也未尝试改停机数据库。

## 保护、兼容、成本与回退

旧1082清单严格核对：只有4个声明既有股票前端源/测试修改、5个股票后端模块新增，其他1078无变化、无删除；4修改前字节副本与旧SHA一致。证据`logs/stock-course-execution-01-preservation.json`和`stock-course-execution-01-backups.json`，不能说旧1082所有文件一字未改。期货/共享认证/网关/布局/配置/旧金融计算均未改。

续接完整当前清单`logs/stock-course-execution-01-current-inventory.json`，允许列表为空；仍用原check-stock-b33-preservation.py对这个新清单verify，不能用旧B33差异覆盖已声明改动。新清单保护当前新增模块，但不抹掉旧基线差异证据。

0源/模型请求、0服务启动、0业务数据库写，无新增依赖或迁移；本地测试产生QA临时文件不当产品行情。回退先核对当前SHA/后续修改，再从四备份生成精确反向patch；新增内部模块未挂载可保留，不能整库/整目录删除或恢复期货文件。

人工路径待用户允许启动：本人股票因子检验选择日期/预处理→“用当前因子配置回测”确认参数→显式开始；检查报告正负柱/零值、v5日胜率/平均换手说明和明细截断。内部DSL/预算/产物/metrics不设置假页面验收，不声称新数据/自定义全功能已可用。

## 下一轮三个原工作包

1. D2：来源/产物元数据表、015迁移/校验、作用域/版本登记及失败恢复；服务继续关闭，真实验证待许可。
2. D4：最小固定数据集/读取、016任务门禁及新方法接口，复用本轮primitive，不再平行新建另一套方法/存储。
3. R2：自定义定义/版本持久化、017迁移和资源API、编辑器到IC/回测的完整接线；不将本轮解释器当已完成CRUD。D1文档/预算补全后接真实适配器，不反复付费试源。
