# 股票研究口径与覆盖体验 Implementation Plan

> 使用executing-plans、systematic-debugging、test-driven-development执行；独立文件范围按dispatching-parallel-agents分工，统一集成验证。用户要求直接继续，不提交、不建分支。

**Goal:** 修复旧/新研究财报择期不一致及默认2018长窗失败，让用户依据真实覆盖选择日期、刷新恢复真实参数，保留所有既有报告和期货行为。

**Architecture:** 保持Next/FastAPI/PG和现有队列。财报选择提取同包纯函数，由底层面板统一使用；新coverage是只读聚合预检，不下载行情、不构建全量面板、不证明PIT或价格合法性。保持既有同步API兼容，新UI仍只创建持久任务。没有结构迁移/新依赖/付费或上游调用。

## 边界与部署顺序

- 已读上一轮记录，源码备份`logs/stock-research-alignment-backup-20261018-0905`，无Git元数据。先完整基线；只读PG统计确认无pending/running后，已暂时关闭研究任务旗标并受控重启，避免升级中父/子进程混用方法；不关闭期货/回放旗标。
- 新方法版本固定`stock-research-2026-10-18.v3`，财报输入选择版本保留`latest-disclosed-report-frontier-v1`；旧v2报告和参数不可改写。幂等重试必须按已存在任务保存的方法/流程版本校验摘要，不能因为当前方法升级让同键同原参数变409或重算。
- 新接口和字段增量兼容，旧调用方不删字段。数据库表不变，不直接改生产Schema。
- 构建命令退出0不等于standalone可部署；Windows EPERM若仍存在明确保留风险，不修改系统安全设置或假装消除。

## 四个工作包

### 1. 财报口径统一与版本兼容

文件：新增`backend/app/services/stock_factors/financial_selection.py`；修改同包`engine.py`、`snapshots.py`；新财务回归测试；主流程修改`schemas/stock_research_jobs.py`、`services/stock_research_jobs.py`和相应测试/HTTP校验固定版本。

纯函数契约：`select_financial_rows(rows) -> list[tuple]`，输入(code,report_date,publish_date,roe,gross_margin,revenue_yoy,netprofit_yoy)。不得假定输入已排序；对有效公告日稳定排序，同公告日最大报告期，后发旧报告期不倒退，最新报告期NULL不补旧值；精确冲突拒绝，缺公告日期保持既有弃用边界且明确不可用于择期。底层`_build_market_data`调用共享函数；快照保留原始全部行指纹/计数并用共享选择获得selected计数，不能双轨悄悄不同。

- [ ] 先写乱序同日年报/Q1、晚发旧期、更正/NULL、公告当天不可用、同步/快照真实SQLite财务面板相同的失败测试。
- [ ] 最小实现共享选择器，方法升级v3；不改原份额/手续费/信号滞后公式、不填价。
- [ ] 保存v2任务夹具，升级当前方法后同键原参数仍返回原ID；不同参数409；新键使用v3；旧报告仍读v2。
- [ ] 运行财务/旧引擎/快照/API/任务回归。真实PG/HTTP验证新旧财务因子同结果，旧v2报告不改。

### 2. 有界真实覆盖预检

文件：新增`backend/app/services/stock_factors/coverage.py`及测试，主流程在`backend/app/api/stock_research_jobs.py`接入`GET /api/stock/research/coverage`。

查询参数：可选start_date/end_date，三种旧日期格式，最大3660天、结束不晚于上海今天。无日期时使用截至本地最新非未来观测日的最近61个观测日；不是固定人工成功区间。认证AuthContext，不依赖研究旗标开启，不接受客户端身份/盘覆盖。独立只读RR聚合事务，SQL/HTTP时间有界。

响应契约（所有日期ISO，计数非负，空范围使用null）：

```ts
type ResearchCoverage = {
  coverage_version: 'stock-research-coverage-v1'; market: 'stock';
  frequency: 'daily'; adjustment: 'forward_adjusted_unversioned'; timezone: 'Asia/Shanghai';
  catalog_count: number;
  available: {first_trade_date:string|null;last_trade_date:string|null;securities:number;price_rows:number;observed_dates:number};
  recommended: {start_date:string;end_date:string}|null;
  requested: {start_date:string;end_date:string}|null;
  selected: {securities:number;observed_dates:number;price_rows:number;missing_price_cells:number;valuation_rows:number;financial_rows:number;panel_cells:number};
  limits: {max_panel_cells:number;max_price_rows:number;max_valuation_rows:number;max_financial_rows:number;max_instruments:number};
  status: 'within_limits'|'no_data'|'over_limit'; warnings:string[]; as_of:string;
}
```

计算panel_cells必须与snapshot相同：price_codes×price_dates + 4×valuation_codes×valuation_dates + (financial_rows>0 ? 4×price_codes×price_dates : 0)。缺价不扣减估值面板，不用计数伪证所有证券/所有日期完整；标明仅统计覆盖，值合法性、日历、授权和PIT仍待实际研究检查。

- [ ] 先以真实SQLite数据验证股/期隔离、目录无价、缺价计数、面板限制/无数据/边界日期/最新61观测日，新增API401/422/来源与追踪测试。
- [ ] 使用聚合/count distinct和日期有界limit，不返回全证券列表或全行情，不进行任何导入。
- [ ] 最终接口返回X-Request-ID/no-store；异常显式503/422，不统一空数组或零掩盖失败。

### 3. 覆盖选择与参数恢复的前端垂直入口

文件：新`frontend/src/lib/stock-research-coverage.ts`及测试、新coverage组件及测试；修改研究workspace和测试。现有任务hook/历史组件无必要不改。

- [ ] 新client按上述Zod契约严格校验、显式token、20秒、AbortSignal，不自动重试；不使用会串用户的全局隐式token。
- [ ] 初始日期不再默认为2018；使用覆盖接口推荐，清楚显示目录与有价证券、实际区间/观测日、缺价/超限警告和来源限制。提供“使用最近覆盖区间”及手动刷新，不宣称推荐区间必定可算或策略有效。
- [ ] 研究日期变更预检去抖/取消旧请求；无数据/已确认超限阻止新建并可修正；预检失败明确错误和重试，不显示过期统计冒充当前输入。
- [ ] 刷新/恢复任务时一次性恢复其日期/类型/因子/预测周期/回测数量权重费率及预处理参数，不让覆盖响应覆盖恢复参数，也不在每轮poll覆盖用户新编辑。切用户/JWT盘重新初始化。
- [ ] 先写失败回归再实现，保留之前所有网络/取消/权限/旧曲线清除的强断言；前端真实生命周期与浏览器验证。

### 4. 集成、回退与真实交付

- [ ] 集成专项→后端全量→前端所有Node测试/tsc→隔离副本build。比较旧XML用例ID防误覆盖测试。
- [ ] 研究旗标仍关闭时先验证旧接口v3/覆盖/期货健康，再开启并受控重启，跑真实HTTP任务/权限/取消、PG并发、原18回放/19扩展（更新版本精确断言但不删断言）。
- [ ] 浏览器使用localhost独立QA身份：无2018默认、覆盖选择、财务因子、刷新恢复参数与tab、历史v2可读、广区间预检提示；保留原用户127.0.0.1标签。
- [ ] 保存实际修改文件/命令/通过失败/金融版本/回退/无模型调用/剩余风险/下一轮三到五包。旗标可回退，旧结果不重算，不还原全库覆盖用户数据。外部部署/数据/AI未验证则明确部分完成，不停用续做自动化。

## 用户新增要求与12:10续做

合并为五包：金融择期/版本/覆盖恢复；期货自定义模拟入金（无月日额度）；tick式A股工作台/六列板块；全市场盘后快照/每日增量统计；自然语言规则解析为可确认配置草稿及集成验证。原安全边界有效，不照搬期货保证金/做空规则到A股。一次性a-12-10已在北京时间12:10实际触发。

09:20附近因额度中断，保留部分落盘，12:10先核实再续做。配置teajoin daily已一次返回2026-10-16未复权5567条、has_more=false；与旧5558目录匹配5548、未知19、缺当日10，缓存于logs/stock-eod-source-20261016.json，尚未导入。不能称已全覆盖。

盘后采用独立版本迁移/快照与运行记录，不写前复权研究表；涨跌依据供应商除权昨收参考，不拿两日未复权收盘直接除。量为手，成交额千元转元，依据https://tushare.pro/document/2?doc_id=27。单次同步最多3次外部请求（日历、目录、当日行情），每次20秒无重试；按日期持久幂等，休市看供应商日历，不猜工作日。初次复用已采集daily避免重扣。缺价不得伪造成停牌价；历史K线和当日快照分别显示覆盖。新旗标默认false，备份/迁移/check后才启用。

## 12:10轮最新交付检查点（17:29记录）

详细文件/命令/红绿测试/迁移回退/真实缺口/人工路径见`docs/STOCK-PRODUCT-VERIFICATION-2026-10-18-1210.md`。本轮五包均已实施，不表示全部生产验收：财报共享择期v3+旧幂等兼容、覆盖预检/参数恢复、无月日额度模拟充值及撮合并发、工作台/六列板块、独立EOD快照与可确认语言配置草稿。

最终后端1757通过/3既有跳过/502警告；前端285通过、tsc退出0；隔离build退出0但standalone EPERM仍在。真实PG资金17/EOD17、隔离8003真实HTTP扩展19/研究23/产品14通过；原1648基线用例ID无遗漏。原8002重启和3001启动被工具策略拦截，没有绕过；新资金/EOD/覆盖恢复完整浏览器验收未完成。浏览器实际验收原3000的六卡、工作台K线和回测/量化语言预填，未创建订单或调用模型。

20261018_003_stock_eod已真实PG首次/重复/check通过，checksum32c748cd87746a1117457dafeb8adaabd473557057dadaad74d9f15ba70910fe；盘后来源5567，目录5558，匹配5548，缺10/未知19，真实导入状态partially_completed。研究qfq表不变。两个真实授权来源请求；模型0调用；每日同步最多3来源请求且不自动重试。源码和PG均有备份，保留新增表前向兼容回退，不还原整库覆盖后续用户数据。

临时8003已关闭；最后17:29探测3000/8002/8003都无监听，原PID不再返回，原因未确认。下轮先重新核实服务状态，不把旧PID/12点截图当当前上线证据。`.env.runtime`存了研究/EOD/read/sync的true，默认示例仍false，只有新进程按配置通过迁移检查后才生效。全系统仍部分完成，下一轮四包以交付记录为准，自动续做不提前停止。
