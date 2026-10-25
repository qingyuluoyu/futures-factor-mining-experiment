# 执行18：页面分工与明确日线选源

承接用户“继续把K线等数据改好，先拆解多个页面一致”，只沿已确认断链执行四包，不新建总规划。执行17接受清单1179/34为基线；root统一保护与交付。服务仍停，不因heartbeat启动、迁移或付费，新增数据/模型预算0，tick263根不认证/混源导入。

## P1 只读日线来源选择（daily agent独占）

复用现有qfq/EOD读取和权限，新增版本兼容的来源目录与显式选源参数；旧API默认行为不改。目录说明来源/复权/本地根数/日期/窗口上限/是否开放，不能用当前价自动拼接历史价。股票详情按目录显式选取最近可用来源，也允许切回历史qfq，显示实际来源和日期；切股/切源取消请求，晚结果不回写，源不可用时明确失败。EOD关闭不能被查询参数绕过。新目录读前验证股票市场/用户，聚合有界超时，不调上游，不认证完整交易日。独占stock.py、stock_eod.py及K线相关测试、stock-kline-contract/client和stock-quote-detail及直接测试。

## P2 因子三页各司其职（research agent独占）

新增类型化view=library/analysis/backtest，保留旧workspace默认兼容。实验室承载现有内置因子说明/自定义版本编辑器，研究只显示IC检验，回测只显示组合参数和结果。继续复用计算、预检、任务与报告，不复制金融核、不添加未接通训练。最近任务为其它kind时显示全局活动状态但不自动回填另一页草稿/报告；保留同一后台任务恢复槽和幂等保护。root另做历史恢复链接/expectedKind契约。

独占stock-research-entry.tsx、factor-research-workspace.tsx及直接测试，新stock-factor-library组件/测试。原factor-lab和backtest路由只在stock分支追加view属性，factor-research默认analysis；不改任何期货分支表达式和函数体。

### 上述股票分发的兼容例外（修改前）

两个共享路由文件含期货业务，但本次仅股票JSX调用参数变化。现有期货分支、imports与函数体逐字节比对不变（新股票依赖如有必须限于原股票入口）；原市场分发、研究入口与全前端回归；原路由备份和精确hunk回退。不得以分工为由改期货布局/表单/认证或删除旧stock workspace参数。root保留旧成功构建为发布回退。

## P3 回放账本四页分工（visible agent独占）

交易保留K线/委托/日期推进。持仓/订单/资产各以其明细为主，共享账户选择与私有数据服务，不挂整套下单/推进/图表。导航与URL对应；账户/用户/JWT盘隔离及不确定写重试不变，只改股票workspace/直接测试和必要纯股票子组件，不修改四个含期货分支的路由。initialTab作为页面角色需正确响应变化，不能只初始化state。

## P4 恢复类型和整合（root独占）

历史恢复链接按analysis/backtest进入对应路由，ResearchRestorePanel新增可选expectedKind；错误类型显示正确入口、不自动应用，旧调用默认兼容；research agent按专页传expectedKind。保持活动任务/未知提交统一拦截，不用按路由分任务槽制造重复写。root独占stock-research-history.tsx、research-restore-panel.tsx及直接测试；不改use-research-task的持久幂等协议。

先红后绿，逐包一次自审和目标验证，整合TSC/全前端/股票接口与权限回归；构建时source仍限定src并恢复tsconfig自动副作用。PG/在线HTTP/本人/迁移发布未执行明确留档。回退仅逐包精确patch，不删数据、不整体覆盖用户修改。所有未完新业务保持关闭，不把选源或页面分工说成全市场历史/可成交回测/ML训练已完整。
