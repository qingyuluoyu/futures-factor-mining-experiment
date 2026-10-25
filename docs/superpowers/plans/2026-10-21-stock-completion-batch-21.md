# B21 股票研究输入资格解释 Implementation Plan

> executing-plans/TDD/verification-before-completion，顺序四包；独立只读review，不提交/建分支。继承期货及共享代码冻结。

**Goal:** 让用户在研究/回测入口明确检查所选因子为何缺输入，区分容量够、原始表有行、存在金融域有效值；不再只能提交后才发现ROE/PE等没有可用数据。

**Architecture:** 新股票独立只读`/api/stock/research/inputs`，严格日期/因子/依赖Schema及JWT股票边界，独立可重复读只读事务、最多四个聚合SQL/5秒单SQL/12秒总时限，统计实际股票价、重复/错误价、按条件可用的估值和披露字段。旧coverage-v1/v2及提交/worker/金融方法不变。股票工作区加入显式点击的输入检查面板，不自动拉源/运行任务、不把possible称完成或PIT认证；参数/身份/盘切换取消并隐藏旧结果。

**Tech Stack:** 既有FastAPI/SQLAlchemy/Pydantic、React/Zod/Node tests、真实PG5433；无新依赖、DDL、配置或共享认证变更，0上游/产品模型/public业务写。

## 范围与口径
- 开工B20-final 990保护通过，无并发实施者。允许stock_research_jobs.py仅include新股票router、股票factor-research-workspace.tsx仅挂载新面板，及新增股票schema/router/service/client/component/测试。旧coverage/engine/snapshots/replay/期货/stock-api.ts/共享网关认证冻结。
- 只接受ISO日期且结束≤上海今天、区间≤3660天，1—9已知不重复因子；中性化或市值权重才加入正有限total_mv。价格固定stock市场、日期及ASCII六位标识；错误收盘/重复组单列，不伪装缺失。
- PE/PB/total_mv只计严格正有限值；其他字段允许有限负数，null、非有限、非正分别计数。估值行必须同证券/同日存在正有效close；财报需report≤publish且publish<请求结束日、证券在区间股票池；这是候选源值，不做历史报告frontier/完整因子重算，更不证明PIT/连续性或可成交。
- 动量/波动至少21个观测日、20日换手至少20日为必要条件；满足只标potential_inputs，仍可能因窗口缺价、前沿择期、中性化、horizon/groups/持仓缺价失败。无日期/字段不能返回零成功；计数0只能是实际SQL结果。

## B21-1 有界只读输入契约
- [x] 先红：金融域正负/缺失/非有限计数、公告当日/未来/前置报告、估值与价格日期交集、期货混入、重复/坏价、缺依赖及最低观察日；严格Schema。
- [x] 新schemas/stock_research_inputs.py、services/stock_research_inputs.py、api/stock_research_inputs.py；旧股票研究router只include。权限/输入错误、超时、SQL失败都不返回空成功；所有路径no-store、trace，日志不含凭据或持仓。
- [x] 输出query回显、字段单位/规则/行数和三类状态invalid_inputs/missing_inputs/potential_inputs、来源局限/只读事务证据；不创建/修改研究任务或把查询计数当数据版本。

## B21-2 用户入口与隔离
- [x] 新client严格验证数量分区、响应query匹配/枚举/日期/状态，捕获token、no-store/20秒/取消，不重试；旧后端404明确未部署，不回退虚假计数。
- [x] 新StockResearchInputCheck显式点击，面板显示字段缺值/非有限/金融排除及因子原因，查询变更/换号/盘/卸载/晚到清理。仅挂股票工作区，不改原任务提交资格或旧coverage契约。
- [x] 行为测试检查入口→loading/成功/错误/重试/参数变更/身份隔离，不出现空白占位或把potential称通过。

## B21-3 真PG/HTTP与回归审查
- [x] 私有schema/JWT/HTTP验证实际浮点/公告/金融域计数、market与owner模式边界、SQL只读和异常路径、public不变、清理；先红后绿、逐包复查。
- [x] 后端受影响/全回归、完整Node/TSC、保护差异及最终hash；独立只读审查关键金融/权限问题并修复后验证，不删除/跳过测试。

## B21-4 真实只读检查点
- [x] 现有真实库受控查询九因子/市值依赖候选输入及HTTP→结果，0源/模型/业务写；不伪称重算、可跑五年或认证。已保存EOD/归档不重复写。
- [x] 保存实际命令/文件/通过失败未执行/金融口径/迁移回退/成本/风险/人工步骤和下一四包。服务/构建/浏览器/发布/人工双市场未验收仍不报整体完成，不删除续做。

## 回退和下一四包
新只读接口/显式面板可独立撤回，无DDL/数据回退或旧协议改变；原任务worker继续最终金融校验。下一四包：9/19来源原因和预算；历史/PIT/复权锚点与五年缺价；可信股票DSL/基准研究/回放扩池；独立服务构建发布回退和双市场人工验收。
