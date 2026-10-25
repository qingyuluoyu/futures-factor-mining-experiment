# A股页面补齐：2026-10-19 07:50 检查点

## 完成状态

用户反馈“A股很多页面没做完”，本轮优先做三个面向用户的工作包：**行情筛选、历史回测/因子实验室、入口与认证状态及真实验收**。三页已接通并通过原服务浏览器与HTTP验证；不是新画的空页或跳到期货模块。**整个系统仍部分完成**，AI看盘、AI交易、策略收藏、消息等股票链路明确标记未接通，不称全功能完成或生产上线通过。

计划：`docs/superpowers/plans/2026-10-19-stock-page-completion.md`。先读取用户AGENTS、原计划/最新交付、页面路由、股票行情/研究接口、scope恢复、模型与相关测试后实施。没有Git仓库元数据，不提交/建分支；未覆盖先前归档修复。

## 根因与实现

- `/market/screener`、`/backtest`、`/factor-lab`原来不在股票路由白名单，且页面只挂期货组件，所以股票侧只能看到安全边界提示。股票分页members、因子目录、coverage与后台analysis/backtest服务实际已存在，不应再建另一套金融计算或用期货保证金规则替代。
- 行情筛选现在使用真实行业目录+50条股票分页，支持行业、代码/名称、排序、仅有日线及到个股详情。行情日/来源/实际覆盖常显，缺价仍是空缺而不是零。长篇同步/归档审计默认折叠，避免筛选首屏完全被状态文字占满。
- 历史回测和因子实验室分别以backtest/analysis为默认页签复用原股票研究链路：认证scope→覆盖预检→参数确认→幂等后台任务→状态/取消→持久化结果→历史报告。没有自动提交，也不挂载期货合成K线、账户或交易组件。
- 同一用户/登录盘仍共享原研究恢复记录和后端活动任务限制。专用入口不会被另一类型旧任务偷偷切换页签；明确提示原任务仍可切换页签查看。普通因子研究保留原来的按任务类型恢复行为。
- 侧栏准确标记仍未接通的股票模块，期货原标签不变；边界页不再错误声称股票模拟交易没做，提供已接通的行情、回放、因子与回测入口。
- 独立审查发现研究入口未列入认证守卫：匿名/退出后可能永远“正在验证”。补`/factor-lab`与`/factor-research`保护路径，并在会话已结束/无效时显示重新登录入口，已先红后绿及真实匿名浏览器核验。

## 实际修改文件

用户入口：
- `frontend/src/app/(main)/market/screener/page.tsx`
- `frontend/src/app/(main)/backtest/page.tsx`
- `frontend/src/app/(main)/factor-lab/page.tsx`
- `frontend/src/app/(main)/factor-research/page.tsx`

组件/路由/认证：
- `frontend/src/components/market/stock-screener-workspace.tsx`（新增）
- `frontend/src/components/market/stock-board-browser.tsx`（StockMembers兼容新增可选refreshKey）
- `frontend/src/components/factor-research/stock-research-entry.tsx`（新增）
- `frontend/src/components/factor-research/factor-research-workspace.tsx`
- `frontend/src/components/layout/sidebar.tsx`
- `frontend/src/components/layout/stock-feature-boundary.tsx`
- `frontend/src/lib/market-routes.ts`
- `frontend/src/hooks/auth.ts`

验证与交付：
- 新增`stock-screener-workspace.test.mjs`、`stock-research-entry.test.mjs`、`stock-navigation.test.mjs`、`hooks/auth.test.mjs`；扩充原`factor-research-page.test.mjs`、`market-routes.test.mjs`。
- `scripts/test-stock-pages-http-local.py`（新增，真实原服务只读；可验证本轮浏览器新建任务）
- `logs/stock-pages-browser-jobs-20261019.json`（两个实际浏览器任务标识，无凭据）
- 本交付、新计划及原扩展计划最新检查点。

没有修改后端生产源码/接口/ORM/SQL迁移、资金或数据导入代码，无新依赖。

## 实际命令与结果

|实际命令|真实结果|
|---|---|
|根目录`node --experimental-strip-types --test frontend/src/lib/market-routes.test.mjs frontend/src/components/market/stock-screener-workspace.test.mjs frontend/src/components/factor-research/factor-research-page.test.mjs`|先红：白名单、组件缺失与默认页签；最终22通过。`logs/stock-pages-red-20261019.log`、`logs/stock-pages-green-20261019.log`。|
|根目录`node --experimental-strip-types --test frontend/src/components/layout/stock-navigation.test.mjs`|未接通标识/过期边界说明先红后绿，2通过；`logs/stock-pages-navigation-red-20261019.log`、`...-green-20261019.log`。|
|根目录`node --experimental-strip-types --test frontend/src/hooks/auth.test.mjs`|匿名研究页缺重定向先红后绿，2通过；`logs/stock-pages-auth-red-20261019.log`、`...-green-20261019.log`。|
|frontend目录`node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs \| ForEach-Object FullName)`|**316 passed，0 fail/skip，2.14秒**；`logs/stock-pages-node-final-20261019.log`。没有删除旧用例或关闭类型检查。|
|frontend目录`node node_modules/typescript/bin/tsc --noEmit --incremental false`|**exit0**；`logs/stock-pages-typescript-final-20261019.log`。|
|根目录`backend/.venv/Scripts/python.exe -m pytest backend/tests/test_stock_market_api.py backend/tests/test_stock_market_board_api.py backend/tests/test_stock_market_overview.py backend/tests/test_stock_research_api.py backend/tests/test_stock_research_coverage.py backend/tests/test_stock_research_engine.py backend/tests/test_stock_research_jobs_api.py backend/tests/test_stock_research_job_service.py backend/tests/test_stock_research_regressions.py -q`|**77 passed，45既有utcnow warnings，5.77秒**；`logs/stock-pages-backend-focused-20261019.log`。没有后端改动，本轮按风险专项回归，未重新跑2216全套。|
|根目录`backend/.venv/Scripts/python.exe scripts/test-stock-pages-http-local.py`|**22项真实HTTP检查通过**；`logs/stock-pages-http-20261018-234317.json`（文件名UTC）。|
|根目录`backend/.venv/Scripts/python.exe scripts/test-stock-pages-http-local.py --browser-jobs logs/stock-pages-browser-jobs-20261019.json`|**26项全部通过**，含两个浏览器新任务实际持久化参数/结果/访问隔离；`logs/stock-pages-http-browser-final-20261019.log`、`logs/stock-pages-http-20261018-234734.json`。脚本本身只读，未创建新任务。|

最初合并测试有1个旧断言失败：原测试在page文件断言scope+token key，认证包装移到entry后该断言定位失效。已迁移到实际entry，仍断言scope+token及入口key，并新增行为测试验证无效scope不挂工作区、用户/盘/token/入口变化隔离；未削弱金融或权限断言。截图发现首屏布局过长后，新增折叠审计红测再改实现，最终全套316通过。独立审查复核P2补丁通过，无剩余确认的重要项；其24项专项和tsc亦实际通过。

HTTP覆盖：匿名401；三页HTTP200仅作为入口辅助证据（不当成UI通过）；真实目录5558/快照5548、50条不重复分页、42银行行业筛选、缺价NULL/仅目录及筛出、涨跌幅排序、非法分页/排序422、no-store/request_id、研究worker就绪、真实300股历史覆盖、原报告读取、不同用户/登录盘404；额外核验本轮两个任务精确日期参数、completed/attempts=1、结果id/版本匹配及他人404。

## 真实浏览器验证（非mock）

使用CUA，在**localhost**新开隐藏QA标签页，登录第二个既有`qa_`账号；原用户**127.0.0.1**的回放标签页只读，未输入/切市场/下单。localhost与127.0.0.1浏览器存储隔离，QA结束后退出并关闭临时页，原页仍在原A股回放账户和2026-09-03日期。

已逐项观察：
1. A股侧进入行情筛选，不再出现未接通边界；真实5558/5548及Oct16来源可见。
2. 行业选银行实际返回42只，点击平安银行到`/market?symbol=000001`，Tick式K线/指标/真实前复权历史可见；不把前复权历史和盘后原价混算。
3. 默认折叠来源审计后首屏可见股票列表；点击下一页实际显示51–100/5558。
4. A股历史回测默认选中组合回测；选2026-07-13至2026-09-11，预检300只/45个观测日/13500价格行/0缺价，才允许新建。
5. QA实际点击新建本地确定性回测：`aac86cfa-1974-4517-b808-80c38f245f1a`，最终completed/100%，报告与真实净值/费用结果出现。不是模拟API返回成功。
6. 进入因子实验室恢复上述旧回测但默认仍为因子检验，提示另一类型旧任务；实际点击新建因子检验`fb667fd0-31ae-4930-8ce4-382f67eadcfa`，最终completed/100%，IC等结果出现。
7. 历史页确实列出两个任务，点击本轮回测可重新读取同一ID/数据版本和报告。
8. 切回期货，原历史回测页保留“历史数据/AI生成K线”、回测配置与报告，未出现A股工作区；没有点任何模型/资金操作。
9. QA退出后分别直达`/factor-lab`与`/factor-research`，均真实跳到`/login`，不再永久验证中。

浏览器任务数据版本均为`a07c62ab2f41819f8e5538a7d918fd66f97481267fef38056ad376139b05ee82`；方法v3/流程stock-research-job-v1仍为原口径。截图：
- `logs/stock-pages-screener-20261019.png`：最终紧凑筛选首屏。
- `logs/stock-pages-screener-bank-20261019.png`：行业筛选，折叠布局修复前截图，非最终首屏。
- `logs/stock-pages-backtest-20261019.png`、`logs/stock-pages-factor-lab-20261019.png`：任务完成与参数。
- `logs/stock-pages-history-20261019.png`：已保存报告复开。
- `logs/stock-pages-futures-backtest-20261019.png`：期货原分支。

## 金融边界、兼容/回退、成本与风险

- 金融计算公式、数据源、撮合和费用口径**本轮未改**。股票研究使用旧前复权本地历史；回测不是未复权模拟撮合或实盘收益，低成本比例模型尚缺最低佣金/印花税/停牌/流动性等验证，报告保留全部限制与“仅供研究”。不把较高IC/IR说成有效策略。
- 当前快照仍Oct16的5548/5558、缺10、未知19；历史只有300只，旧数据来源证据/复权锚点未认证。没有抓Oct19未收盘数据、造价格/量、填缺省因子或批量重导。
- 仅兼容增加可选组件props和三个精确股票路由，无新HTTP接口/DB结构/迁移，原期货分支保留；回退可恢复本轮前端分支/路由/组件，**保留QA已生成的有效研究记录**，不回滚数据库或资金。没有服务重启，07:45实查3000 PID7268/8002 PID10784/PG5433 PID3532仍监听；下次操作前需重核，不据旧PID kill。
- 成本：主动新增Teajoin/付费模型调用0；仅两个QA本地研究计算任务，HTTP脚本只读。原运行服务自己的WS/既定调度未停用，不据此宣称系统无任何网络活动。无新依赖/真实资金动作。
- 未执行完整生产构建/Windows发布回退、全部期货回归或真实模型联调；浏览器仍有既有开发环境Issue提示（前轮已记录主题hydration），未在本轮擅自修整全局主题。无正式用户发布验收，不称生产可用。
- 仍未接通：股票AI看盘、AI交易、策略收藏夹和消息；本轮只明确标记，不用空页面/期货数据伪装完成。多浏览器标签并发最终由后端限额与幂等保护，本轮没有宣称前端跨标签全局锁。

## 人工路径与下一轮五包

登录→切A股→行情展开“行情筛选”：行业/代码/缺价筛选→股票详情。点击“历史回测”或“因子实验室”：先核对覆盖→选日期及参数→明确点击开始→等待后台结果→历史研究报告复开。若数据覆盖不足/服务关闭，保留错误及禁用状态，不应绕过预检。切期货应回到原功能；不需要真实账户。

下一轮优先响应页面缺口，不继续只做内部归档：
1. A股只读AI看盘：真实行情/财报工具白名单、引用与数据版本、结构化输出、用户权限、预算/超时/固定评测；不得用期货裸合约和保证金上下文。
2. A股AI交易先完成“自然语言→Schema配置草稿→用户确认→日线模拟任务”完整链路；不能让任意模型文本直接下单或动真实账户。
3. 股票策略收藏夹：独立市场/用户/登录盘契约、版本化迁移和真实任务配置保存/恢复，不混入期货收藏。
4. 股票消息：研究完成/失败等真实事件、权限与分页、可追踪通知，替换演示内容，不造消息。
5. 历史数据/覆盖正确性：因子与可得时点/复权锚点、缺10/未知19受控对账与真实收盘证据；有限预算、先兼容迁移，不能以页面接通替代数据认证。
