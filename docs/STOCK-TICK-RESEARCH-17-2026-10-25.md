# 执行17：股票回测参数交互

状态：**已实现待验证**。当前源码的94项目标回归及TSC通过；未启动服务、构建、迁移、HTTP或本人浏览器验收，不能称发布完成。本次续做承接14:01已绿色的源码，没有重写或覆盖上轮实现。

## 实际范围与实现

三个交互工作包复用既有股票研究任务链，第四包为目标验证与一次自审：

1. 左侧因子目录在回测页签真正增删`btFactors`，高亮和`aria-pressed`一致；检验因子的单选状态独立。选择变化进入既有因子依赖覆盖预检，旧覆盖不能放行新组合。
2. 组合策略参数集中展示，手续费/滑点以基点输入，草稿保留空值和非法输入供纠正；范围0–100基点，与旧Schema的0–0.01比例范围相同。3基点仍提交0.0003，10基点仍提交0.001；已保存比例原值保留，显示转换避免普通浮点长尾。非法、空、非有限及越界值不能提交。
3. 当前任务提供显式“恢复当前任务参数”。复用已存在的历史草稿Schema/目录校验、日期/预处理/因子/调仓/数量/权重/费用回填，取消旧覆盖请求、清理草稿结果、重新预检，必须另按开始按钮。忙碌、活动任务、提交结果不确定及恢复待确认时禁止替换；旧事件处理器也检查最新任务状态。原任务与报告不被改写。

参考只读核对`F:/111/tickflow-stock-panel-main/frontend/src/pages/backtest/StrategyBacktest.tsx`的参数区及费用转换：该项目佣金是`Number(fees)/10000`，滑点请求是独立`slippage_bps`。本仓库二者接口均为比例，因此本次仅借鉴基点交互，不复制其不同请求契约，不增加未实现的分钟成交、风控或ML按钮。

修改文件仅：

- `frontend/src/components/factor-research/factor-research-workspace.tsx`
- `frontend/src/components/factor-research/factor-research-page.test.mjs`

`stock-research-entry.tsx`未修改，SHA与执行16接受清单一致。期货、共享布局/认证/网关/运行配置、金融核和PIT未修改。新增仅本文及独立证据文件，不修改AGENTS、总计划或总清单。

## 命令与真实结果

历史证据：`logs/stock-tick-research-17-before.json`与同前缀backup保留字节；`red.log`为28通过/4失败，四项因缺少新控件失败；`green.log`为32通过/0失败。两个历史日志未包含调用命令行，续做不能声称核实了未记录的历史命令，也未回退现有源码重放红期。

本次续做在`frontend`实际执行：

```powershell
node --experimental-strip-types --test src/components/factor-research/factor-research-page.test.mjs src/components/factor-research/research-restore-panel.test.mjs src/components/factor-research/use-research-task.test.mjs src/components/factor-research/stock-research-entry.test.mjs src/components/factor-research/research-coverage-panel.test.mjs src/lib/stock-research-api.test.mjs src/lib/stock-research-coverage.test.mjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

结果：94通过、0失败、0跳过；TSC退出0、无输出。覆盖实际组件处理器、旧任务恢复/会话边界、覆盖请求失效、接口Schema、费用转换和显式提交。VM组件交互回归不代替本人浏览器验收。

一次定向自审已完成：核对上轮字节差异、真实选中参数、检验与回测状态隔离、费用上下界及比例恢复、旧覆盖取消、活动/不确定任务保护和旧接口调用链，无需继续源码修改。只读差异命令为`git diff --no-index -- logs/stock-tick-research-17-backup/factor-research-workspace.tsx frontend/src/components/factor-research/factor-research-workspace.tsx`，退出1表示存在预期差异。初次尝试系统`python -c`生成差异因`No module named pathlib`失败，改用只读git差异；此工具失败与产品测试无关，没有掩盖验证失败。

SHA、两个精确patch、冻结入口SHA及命令结果记录见`logs/stock-tick-research-17-resume-verification.json`；生成命令`node logs/stock-tick-research-17-resume-artifacts.cjs`同时验证两份原字节备份SHA。

## 口径、兼容与风险

金融方法、价格/财务/PIT口径、滞后信号、成交/税费逻辑、数据库和旧API均无变化。费用只是显示单位转换；原API继续提交比例值，旧任务按Schema恢复。无迁移、依赖、额外模型或数据调用，新增外部费用0。恢复参数触发既有本地覆盖GET，不自动提交或执行研究。

剩余风险：尚无运行中的当前构建、真实HTTP与本人浏览器验证；研究回测原有历史修订、公司行动、停牌与真实撮合资格限制继续以报告方法说明为准。本包不声称完整ML产品或可实盘验收。当前全部端口停止，不自动拉起。

回退：仅在确认没有后续用户修改时使用已核SHA的原字节备份；出现后续修改时按两个精确patch反向恢复本包差异，保留其余修改。无数据库回滚事项。

## 人工验证路径及下一轮

在后续获授权的当前构建中以A股身份进入组合回测：左侧增删两个因子，确认高亮/多选与预检刷新一致；输入2.5基点/8基点，独立确认提交请求为0.00025/0.0008；留空或输入负值/101基点应阻止开始。完成任务后改日期与费用，再点恢复当前任务参数，确认参数回填、重新预检且没有自动POST。任务活动/恢复中/提交不确定时恢复与目录选择应不可用。切回检验确认原单因子选择保留；期货入口维持原行为。

下一轮三包由root安排：当前源码集成构建与可回退预览；真实身份下上述浏览器/HTTP验收；既有ML正式任务/API/UI垂直链继续推进，不重复本包或新建计算核。
