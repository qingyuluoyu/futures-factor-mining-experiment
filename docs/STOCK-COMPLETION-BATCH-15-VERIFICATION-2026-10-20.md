# B15 期货保护与A股组合筛选真实检查点

记录时间：2026-10-20 22:37，Asia/Shanghai。状态：四个限定工作包代码实现、本机自动化及真实隔离PG/HTTP验证完成；未发布、未完成实际浏览器/人工验收，整个系统仍部分完成。

## 范围和数据流

1. 建立产品源码/共享配置哈希保护，冻结期货及共享认证、模型网关、布局、运行配置；不停止、启动或抢占既有服务，不更改账户/任务/行情状态。
2. 新增股票GET `/api/stock/market/screen`：股票权限→Pydantic Query→既有市场快照→独立确定性AND筛选→有界分页。价格上下限为CNY/股，涨跌幅为百分数，成交量沿快照单位，不截断小数。只有data_available=true且相应值有限的行参与数值条件；合法0保留，缺失不补0。原来源、日期、调整、质量元数据保留，不额外采集数据。
3. 股票专用多条件入口显式打开；草稿不自动查询，应用/重置、排序、50条分页、详情跳转、错误/重试和单位展示。用户/市场/JWT盘/登录代次变化取消并清空，旧响应不能恢复新会话状态。旧基础股票列表和期货页面保持原路径。
4. 响应stock-screen-v1回显规范条件；全筛选集合及元数据SHA256绑定后续分页，快照变更409要求重应用，不混旧新页。此指纹仅保证分页一致性，不是不可变历史数据版本或来源真实性认证。

新端点无缓存、带request_id，范围/非法数/额外字段422；匿名401、期货市场409；读取超时/数据库失败503而非空成功，错误不暴露数据库细节。旧boards/members/details接口未改，不将筛选结果暗示为完整历史池或可模拟交易集合。

## 实际修改文件

- 计划：`docs/superpowers/plans/2026-10-20-futures-preserved-remediation.md`、`docs/superpowers/plans/2026-10-20-stock-completion-batch-15.md`、主计划`docs/superpowers/plans/2026-10-19-stock-product-completion-master.md`、本检查点。
- 保护：新增`scripts/check-futures-preservation.py`、`backend/tests/test_futures_preservation_guard.py`。
- 后端：新增`backend/app/schemas/stock_screen.py`、`backend/app/services/stock_screen.py`、`backend/tests/test_stock_screen.py`；仅修改股票`backend/app/api/stock_market.py`增加端点和导入。
- 前端：新增`frontend/src/lib/stock-screen-api.ts`及`.test.mjs`、`frontend/src/components/market/stock-screen-results.tsx`及`.test.mjs`；修改股票`stock-screener-workspace.tsx`及其测试；`frontend/src/lib/stock-market-api.ts`仅导出既有members Schema供复用。
- PG验证：新增`scripts/test-stock-completion-batch-15-postgres.py`；旧`scripts/test-stock-completion-batch-02-postgres.py`仅增加b15报告标签，不是期货生产代码变更。

没有新增依赖、生产DDL、金融计算方法或资金/手续费规则变化；没有提交/创建分支。

## 实际命令与验证

以下命令在仓库执行；Node/TSC命令的工作目录为frontend。日志位于logs，不含密钥。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-futures-preservation.py snapshot logs/futures-preservation-b15.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_screen.py backend/tests/test_stock_market_board_api.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_futures_preservation_guard.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-15-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/check-futures-preservation.py verify logs/futures-preservation-b15.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-futures-preservation.py snapshot logs/futures-preservation-b15-expanded.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-futures-preservation.py verify logs/futures-preservation-b15-expanded.json
```

- 产品改动前基线：后端2615通过/3既有skip/703warnings（含新增保护器测试），Node470通过，TSC退出0。证据`b15-backend-baseline.log`、`b15-node-baseline.log`、`b15-tsc-baseline.log`。不宣称零warning或本轮增加skip。
- 新后端业务反例最初10失败（端点/模块缺失），实现后通过。复查增加数据库错误no-store、全快照分页变化反例，均先失败后修复；最终股票目标15通过/39warnings。证据`b15-screen-red.log`、`b15-error-red.log`、`b15-snapshot-red.log`、`b15-screen-targeted-final.log`。
- B16记录纠正：上述目标命令原文误写不存在的`test_stock_market.py`，按B15目标日志实际出现的文件纠正为`test_stock_market_board_api.py`，没有重写原日志或变更原15通过结果。
- 新前端最初缺模块属于测试接线失败，不冒充金融断言红；另有实际分页指纹缺失行为反例，修复后目标10通过。证据`b15-client-red.log`、`b15-page-fingerprint-red.log`、`b15-client-reviewed.log`。
- 最终完整后端：**2628通过、3既有skip、733warnings，152.10秒，退出0**。`b15-backend-confirmation.log`。原套件包括通知AsyncMock未await等warnings，未为消warning改无关生产代码。
- 最终完整Node：**476通过、0失败/跳过**；TSC退出0。`b15-node-confirmation.log`、`b15-tsc-confirmation.log`。没有关闭类型检查或删除旧测试。
- 最终专用本机PG5433、真实JWT及ASGI HTTP：**64检查通过、私有schema清理成功**。`stock-completion-b15-pg-20261020-143412.json`、`b15-pg-confirmation.log`。真实SQL验证AND/分页/空价/未来及期货行排除/零量/旧接口不变；在私有表改价后真实HTTP409；匿名/跨市场拒绝；原验证器三个期货账户余额、订单及账本未变，公共行情目录行数未变。测试证券和行情为明确私有合成fixture，不作实际行情真实性认证；模型工厂为测试fixture，不调用真实供应商。

## 保护基线与独立复查

- 原基线`futures-preservation-b15.json`894文件，保留未覆盖。初次verify通过。独立只读审查发现P2：遗漏globals.css和7个实际依赖/部署配置。
- 增加反例1失败1通过→修复后2通过（`b15-guard-review-red.log`、`b15-guard-review-green.log`），保护CSS/额外源码后缀以及实际pyproject/uv.lock/tsconfig/postcss/docker配置。
- 扩展保护器再对原清单verify**预期失败**：`b15-guard-expansion-diff.log`恰列8新增、仅保护脚本自身hash变化、无删除。原894中其余893文件当前均匹配原哈希，不是期货/共享生产代码变化。
- 新基线`futures-preservation-b15-expanded.json`为902文件，最后verify **passed，removed/added/changed均空**（`b15-futures-preservation-confirmation.log`）。新增8文件的起点是审查扩展时，不能追认任务开始时的状态，不能把扩大清单当消除任意已有改动。
- 独立只读复核确认该P2已修、未发现新的具体P1/P2，独立计算哈希与上述一致；复核未改文件、未访问网络/数据库或重跑测试。此结论只覆盖本批，不代表全系统无未知缺陷。

## 兼容、迁移与回退

- 无数据库迁移、回填或数据重导；原期货接口和运行配置不变。新前端依赖新screen契约，旧后端404/不合法响应明确报错，不静默退为无条件列表。
- 发布顺序：先单独验证新后端screen能力，再验证新前端入口。尚未执行发布。退回时只回退新增股票多条件入口/端点和对应股票文件；默认基础筛选保持可用，无数据库逆向迁移，不恢复曾修复的安全/金融错误。
- 一旦保护清单出现未解释漂移，停止部署并核对，不直接复制基线覆盖用户修改。期货生产逻辑没有本批变更可回滚；后续涉及共享认证等必须独立兼容、完整期货回归后另包发布。

## 成本、未执行和剩余风险

- 上游行情调用0、真实付费模型调用0、公共业务写0；新增开销为有界当前快照筛选及SHA256，不将有限并发/超时/参数限额误删为业务次数限制。
- 本轮未启动/重启/结束服务，未执行生产构建或实际浏览器/用户人工验收。轮初观察8002/6380/8110离线，3000被非项目进程使用，3001已有监听但未核验用途；不冒充现在在线。PG5433本轮实际使用成功。
- B13 standalone构建EPERM仍未解决；旧历史/回放/五年缺价、S04服务端持久人工确认及幂等、S05服务端记忆/对话市场盘隔离、真正期货因子研究、GP可信来源/发布、股票AI及配套链路仍未在本批解决。原启动_init_tables调用已被前批去除，完整存量迁移仍未齐；导出/历史参数和部分估值金融修复已有实现，不能重新说全部未做。
- 数据覆盖只引用21:20前批只读证据：目录5558/110行业；19日快照5545/5558、13缺/19未匹配；旧历史300只585392行至16日；回放3只；影子2日11093行；五年8931缺价，600221缺估值持仓价仍拒绝。**本批没有重查/更新20日收盘，不作全历史认证。**
- 自动化测试/哈希证明本批范围内未改期货生产文件及覆盖用例通过，不等于所有既存期货功能均经人工验证。

## 人工路径（未执行）与下一四包

安全恢复专用项目服务后：本人登录→切A股→股票筛选→打开多条件筛选→填写价/涨/量并应用→核来源/日期/单位→翻页/改条件/重置→点股票详情；模拟快照变化应提示重应用；登出换号/快速换市场不出现旧结果。再切期货检查原筛选、行情、资金、订单/任务入口不变。服务端权限/金融拒绝不可因人工操作取消。完成后记录浏览器及人工结果，不以本说明代替验收。

下一轮按依赖执行：
1. 股票目录/当前行情/连续历史/研究资格/回放资格分层展示及只读覆盖预检，不扩大当前可交易范围。
2. 13缺/19未知与五年缺价的来源、历史时点和有界补采预算清单；先证据后执行，不盲导或补0。
3. 股票研究数据版本读取、自定义因子/可信基准契约及旧消费者清点；缺数据保持失败，不假称五年通过。
4. 项目专用服务、构建/发布回退及双市场人工验收另包推进；共享安全/期货遗留另开受保护兼容范围，不夹带到股票切片。
