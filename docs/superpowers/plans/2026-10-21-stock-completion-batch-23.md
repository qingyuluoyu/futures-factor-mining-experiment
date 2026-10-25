# B23 A股模拟委托全历史检索 Implementation Plan

> 使用writing-plans、executing-plans、TDD；当前会话顺序四包，独立只读review。不得提交/建分支、改期货或共享认证/网关/布局/配置。B22-final 1003保护开工通过，既有工作不覆盖。

**Goal:** 修复A股模拟盘只能看到全部待处理及最近200笔历史委托、再早的已持久记录没有入口的问题，提供本人/登录盘隔离的真实筛选和服务端分页。

**Architecture:** 新股票独立历史schema/service/router，父stock_replay仅import/include工厂，继承原ready/开关/迁移与股票门禁。新`GET /api/stock/replay/accounts/{account_id}/order-history`查询本人的同盘账户，独立只读可重复读事务核对expected_revision，避免推进/撤单后跨页混状态；三业务SQL（账户/过滤计数/分页）。股票工作区原近期委托/撤单保留，新增显式全历史子面板，不新增交易操作或更改撮合/资金/幂等。

**Tech Stack:** 现有FastAPI/Pydantic/SQLAlchemy/PG、React/Zod/Node测试；无DDL/依赖/配置/外部源/模型调用/public业务写。

## 明确边界与契约

- 已读stock_replay API/schema/service/engine/models、原回放API和工作区/测试。根因是view历史SQL limit200，加前端slice只分页已返回数组；数据未删除。不能去掉SQL上限返回全部，也不能只前端改页数。
- 白名单既有生产只`backend/app/api/stock_replay.py`和`frontend/src/components/trading/stock-replay-workspace.tsx`，均仅import/挂载。原撮合engine、service、schema、模型/迁移和stock-replay-api.ts全部冻结。新单独模块不能调交易写函数。
- Query：required expected_revision整数0—2147483647；status all/pending/filled/rejected/cancelled；direction all/buy/sell；可选account绑定主板code、ISO start/end；limit1—100、offset0—100000。日期倒置或晚于账户游标422，不读取未来；未知/他人/不同登录盘账户统一404；revision变化409需刷新账户，不重试旧页/自动操作。
- 返回query规范化回显、account_id/revision/current_date/mode/method/dataset_bindings（实际已绑定快照ID，不冒充内容版本）、total/items/has_more、稳定created_at desc/id desc、read_at/只读证据/费用币种单位、非PIT/日线模拟限制。每条订单来自已存记录；价格/金额用有界有限Decimal字符串，不用JS Number复算账本。非法状态日期/金额拒绝，不改零或空成功。
- 保留固定快照实际规则：只主板、T+1、下一开盘、公司行动阻断、真实价缺失拒绝，方法stock-replay-v1不改。订单历史不是券商账单、实际成交或收益认证。分页expected_revision不是不可变版本读；账号有新变动必须刷新，不暗中沿用新revision。
- 单SQL5秒/锁2秒/接口12秒/客户端20秒；0自动重试/后台全量翻页；业务次数不限，参数/并发/权限/费用边界保留。

## B23-1 后端完整切片
Files：新增`backend/app/schemas/stock_replay_history.py`、`services/stock_replay_history.py`、`api/stock_replay_history.py`、`backend/tests/test_stock_replay_history.py`。原`api/stock_replay.py`仅include新router工厂并传既有replay_service依赖。
- [x] 写并观察红测：>200记录过滤分页并集、跨用户/盘404、revision409、日期/代码/Schema/金额有限边界、SQL只读/上限/不写、不混期货；真实SQLite，不用mock替代金融行。
- [x] 实现三查询，只选当前用户同盘且规则受支持的账户；status/direction/code/提交日期过滤，读取limit+1、排序/总数/状态一致性。错误带trace/no-store，金融异常409，DB/超时503不返回假空页。
- [x] 原回放接口/引擎/模型/写入契约测试保持，不更改金融方法或恢复业务次数限制。

## B23-2 用户入口和前端隔离
Files：新增`frontend/src/lib/stock-replay-history-api.ts`及test、新`frontend/src/components/trading/stock-replay-order-history.tsx`及test；原workspace仅import/在委托区挂载子面板。
- [x] client先红后绿：严格枚举/UUID/金额/日期/数量/分页及query/account/revision回显校验，捕获token/no-store/取消，409要求刷新账户、不盲重试或回退近期200。
- [x] 面板先红：显式筛选/读取、前后20条/正常空页/错误/手动重试；user/token/市场/盘/account/revision/参数变化立即隐藏并取消旧结果，晚到不覆盖。原撤单入口保留，新面板只读并说明全历史/近期区别，不暴露高权限工具。
- [x] 行为测试真实转译面板及工作区挂载，不仅搜索文案；完整Node/TSC通过。

## B23-3 真PG/HTTP与审查
Files：新增`scripts/test-stock-completion-batch-23-postgres.py`、`scripts/check-stock-b23-preservation.py`及guard测试；原PG harness只允许新b23标签。
- [x] 私有schema真PG/JWT/HTTP：真实StockReplayService创建/买单/推进撮合/撤单并读历史；额外私有历史fixture>200验证翻页；本人/他人/盘/股票与期货边界、revision冲突和异常/超时/只读；public完整账本/任务/股票回放表摘要不变，清理schema。
- [x] 目标/完整后端及Node/TSC、保护hash、独立只读金融/权限审查；失败先定位，不删/跳测试或修改冻结期货。

## B23-4 检查点和回退
- [x] 受控只读真实库：记录是否有可授权本人账户；无授权账户不创建生产测试用户/资金，只报告未执行实用户验证。PG真实撮合证据明确为私有夹具，不能认证市场数据/实盘收益。核对public状态前后不变。
- [x] 保存真实文件/命令/红绿和失败/未执行、金融口径、成本、迁移回退/人工路径/下一四包，更新主/保护计划，不称全系统完成或删续做。

## 验证与回退
目标pytest新history/guard及原replay集；新私有PG脚本；全部Node、TSC、完整pytest；原/最终保护verify。无DDL或public写，回退仅撤本包import/include和子面板挂载/新模块，不恢复整文件；原近期委托和撤单/撮合保持。下一四包：来源9/19原因/补采预算；历史PIT/锚点/五年缺价；可信DSL基准/回放扩池；独立运行构建发布/双市场人工验收。
