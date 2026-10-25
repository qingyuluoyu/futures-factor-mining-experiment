# B27 股票行情与财务响应完整性检查点

## 状态

四包股票消费边界改动已实现并通过本机验证、只读PG/HTTP和本人页面正常路径；全系统仍部分完成。股票AI、PIT/历史复权锚点、五年研究/基准/回放扩池、收藏消息及正式发布验收不因此标完成。没有绕过原股票聊天409/期货工具权限或供应商429。

## 文件与实现

- `frontend/src/lib/stock-market-api.ts`：个股详情必须与请求代码及交易所标准标识一致；日期使用真实ISO日历校验。估值不能晚于响应as_of，财报须report_date≤publish_date≤as_of，拒绝重复披露；覆盖数量与起止日期一致。负PE/PB/ROE/EPS/BPS、亏损、0和null按原金融含义保留，不做字段转换或过滤掉亏损证券。
- 同文件：coverage须可比≤有价≤目录；行业涨/跌/平之和=可比，样本数、领涨和缺值语义一致，可见行业合计不得超过全局覆盖。证券/行业标识唯一；成员价必须正、量可为0或小数但不得负，状态/数据可用标志/日期一致。分页列表有界，不将分页样本强行等同全池；正常成员API限本次50条，筛选仍可100条；非法offset在网络前拒绝。
- `frontend/src/lib/stock-screen-api.ts`：改用Zod safeExtend保留原schema新增refinement，避免普通extend丢/拒约束；请求过滤回显和单位校验保留。搜索、覆盖面板调用方已核查，没有返回结构破坏。
- `frontend/src/lib/stock-market-api.test.mjs`：增加错证券/错后缀、无效日期、未来披露、覆盖矛盾、重复行/行业、统计矛盾、0价/负量、非法分页和原负值/null/0保持等反例。
- 新`scripts/verify-stock-market-contract-local.py`：仅loopback PG5433，以REPEATABLE READ+数据库READ ONLY连接调用原股票ASGI路由，记录真实boards/members/details/screen，检查请求编号/期货上下文拒绝及原AI任务摘要；不伪造生产JWT，不读取私人账户。
- 新`scripts/check-stock-b27-preservation.py`、本记录、B27子计划和主计划最新指针。没有后端生产/共享认证/网关/布局或金融计算代码变更，无DDL、模型/行情源调用、新依赖、提交或分支。

## 验证证据

- 初始10测中4项先红（原代码会接受错误证券/日期/统计/成员状态），实现后目标三文件17通过。复查新增全局样本上限及offset两项先2红，再修复；50/51条分页边界为补充回归，未伪称该项先红。
- 全量Node最终 **601通过、0失败/跳过**：`logs/stock-b27-node-final.log`。使用项目既有别名加载器，没有跳过或放宽断言。原593基线B26在开工保持，旧错误命令的40失败不重新声称产品缺陷。
- 股票后端相关 **40通过/39既有警告**，`logs/stock-b27-backend-affected.log`；本轮缩小的期货安全回归 **81通过/272既有警告**，`logs/stock-b27-futures-safety.log`。不是全量后端、真实资金事务或所有期货功能认证。
- 真实PG只读/ASGI **13检查通过**，`logs/stock-b27-real-pg.log`和`logs/stock-b27-real-contracts.json`。四份实际响应分别通过最终Zod/筛选客户端契约，`logs/stock-b27-real-zod-final.log`；没有用mock取代数据库、没有生产JWT权限验收冒充。内部工具权限来自固定本地操作员fixture，原运行匿名鉴权另验。
- 完整TSC exit0，`logs/stock-b27-tsc-final.log`；独立production构建exit0，`logs/stock-b27-build-final.log`，目录`.next-stock-b27-final`。首个产物早于复查修复，未当最终部署；最终构建包含复查修复，不覆盖运行产物或默认.next。
- 运行HTTP9检查通过，`logs/stock-b27-runtime-final.log`：原main/股票worker运行，GP原false不改，原1项AI交易仍paused/user。模型调用未新增，旧失败请求仍rate_limited而非假成功。
- 本人已有会话浏览器：110板块加载、无新格式错误；000001.SZ显示平安银行及原前复权K线、财务摘要/公告日期均可读；未提交研究/资金/交易/模型。结束恢复板块预览。

## 实际主要命令

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-stock-market-contract-local.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_kline_reference.py backend/tests/test_stock_market_api.py backend/tests/test_stock_market_board_api.py backend/tests/test_stock_market_capability.py backend/tests/test_stock_market_overview.py backend/tests/test_stock_screen.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_paper_custom_funds.py backend/tests/test_paper_matching_funds.py backend/tests/test_trading_mode.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_ai_chat_execution_safety.py -q
node frontend/node_modules/typescript/bin/tsc --project frontend/tsconfig.json --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b27-preservation.py verify logs/futures-preservation-b27-start.json
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-ux-runtime.py
```

frontend目录：`$files=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName); node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files`。

真实契约复核：Node通过既有别名加载器导入stockBoardsSchema/stockMembersSchema/stockDetailsSchema，对`logs/stock-b27-real-contracts.json`四份原始实际响应逐份safeParse；screen用getStockScreenApi在离线fetch适配中解析该真实响应。此步骤不发网络，不能冒充本人浏览器取数；真实PG/ASGI与本人页面是另外两层验证。

构建在frontend，进程env WENYUAN_BUILD_DIR=.next-stock-b27-final、WENYUAN_PREVIEW_BUILD=1、NEXT_PUBLIC_CHAT_SAME_ORIGIN=1、BACKEND_URL=http://127.0.0.1:8002，执行`node node_modules/next/dist/bin/next build`。Next生成配置用apply_patch去临时类型include后，使用原writeConfigurationDefaults生成器恢复原v3 include和Windows结尾；最终 **1046受保护文件逐字节无漂移**，`logs/stock-b27-preservation-final.log`。没有放宽保护或覆盖baseline。

## 金融口径、兼容与回退

本轮只拒绝不一致响应，不新增计算、补0、截量、修复历史或独立认证来源。财报相对于as_of日期合法不代表历史修订可得时点/PIT已认证；价差仍分别使用原qfq邻K/原价供应商参考，不混21日原价和16日qfq。

旧qfq可选追溯字段继续兼容，原输出字段/数值单位保留。原本可被消费的矛盾响应现在显错，由B26独立通道保留另一成功视图，而不是隐藏坏行、返回假空数组或放宽样本。严格校验可能暴露其他存量质量问题；应保留证据按源整改，不能删守卫。

public业务写0，新增来源/模型调用0；旧后台自主新闻/行情调度仍运行，其既有费用未测。无数据库迁移、无新运行配置文件。只核对PID文件+端口+Next命令属于本对话后切3001，8002/3000及其他服务未停止、任务未恢复。

回退可在本人管理的3001切回`.next-stock-b26-final`，或逐项恢复本轮三个股票客户端文件；保留数据/请求幂等和暂停意图，不删表或补价。无Git提交，不虚构一键revert。

人工路径：http://127.0.0.1:3001/market→行业→个股→估值财务。异常响应反例仅在自动测试注入，不修改实际行情或人为制造错误页面。

## 下一三包

1. 股票只读Agent独立上下文/持久幂等/引用和总预算；供应商429未解决前不无预算复测，不复用期货高权限工具。
2. 来源授权和费用预算明确后的历史原价/锚点/PIT、缺口核验与可信回放集合；先只读审计、不拼复权或盲重导。
3. 股票研究收藏/配置恢复及独立消息事件；按用户/市场/JWT盘持久隔离。正式发布/容量/迁移回退和全站人工验收仍未通过，自动续做保留。
