# 执行18 P3：A股回放交易与账本页面分工

状态：已实现待验证。目标前端23/23通过；服务停止，本包未启动服务、构建、迁移、连接数据库或外呼。统一TSC/全前端/构建由根整合，真实HTTP与本人浏览器尚未验收。

实际文件：修改`frontend/src/components/trading/stock-replay-workspace.tsx`、直接测试`stock-replay-order-history.test.mjs`，新增同目录`stock-replay-pages.test.mjs`。不改四个含期货分支的路由、共享侧栏、认证、K线组件、研究、后端或运行配置。

## 实现与兼容

- `/trading`默认overview：保留账户标的、原价回放K线、下单、下一交易日推进及原撮合说明，增加当前/近期委托表格。查看完整委托历史通过订单页。
- `/positions`：所选私有回放账户的持仓账本为主，展示当前回放日/版本、持仓及次日可卖数量，不挂K线、下单或日期推进。原持仓单元格的“选中后在同页下单”按钮改为静态证券信息，买卖通过显式交易回放链接进入。
- `/orders`：完整委托历史查询入口及既有当前/近期委托、撤单功能，不挂交易工作区。已结束账户仍可查历史，不能通过显示角色绕过原撤单限制。
- `/assets`：原账户资金/盈亏/费用及固定版本追溯为主，不挂交易工作区。

`initialTab`参数及默认值保留，但直接作为页面角色，不再复制到本地tab state；父级角色变化立即同步内容。四个页面标题、主内容和`NextLink`目的路径对应，使用`aria-current=page`标明当前页，不再在持仓URL内仅切成本地委托tab。共用原账户选择、分页、独立账户创建、服务状态和摘要，不复制数据服务或金融内核。

写入中或结果未确认时，本页新增资源导航显示禁用文字、账户选择保持锁定；“重试同一请求”继续使用同一个账户、expected_revision和idempotency_key。共享侧栏及浏览器离开页面的既有行为未在本包变更，不声称新增了跨页面/刷新后的写入恢复协议。

身份/用户/JWT盘上下文、私有账户服务、取消旧读取/忽略晚响应、账户记忆键、自定义初始资金、费用/T+1/百股与成交量规则不变。角色是展示职责，不是权限授权。11个现有读取/写入与校验入口相对字节备份的AST文本逐项相同，证据`logs/stock-replay-pages-18-financial-boundary.json`。账本仍复用原ReplayView响应（含既有chart字段），本包仅避免挂载K线画布，不宣称减少了服务端行情读取或响应字节数。

## 红绿、自审与命令

新增角色/交易/即时角色同步测试先红，初次7项4绿3红（缺专页、交易无近期委托、角色状态不响应），证据`logs/stock-replay-pages-18-red.log`。实现后目标23/23、0失败/跳过，证据`logs/stock-replay-pages-18-green.log`。在`frontend`执行：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/trading/stock-replay-pages.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/trading/stock-replay-pages.test.mjs src/components/trading/stock-replay-order-history.test.mjs src/components/trading/stock-replay-state.test.mjs src/lib/stock-replay-api.test.mjs src/lib/stock-replay-history-api.test.mjs
```

覆盖三个账本不挂图/交易表单/推进、默认交易仍保留原图表单位和安全涨跌参考、URL链接/即时角色变化、切账户先清旧视图、未确认撤单的原键/版本重试及恢复导航、身份/市场/盘变化拒绝写入、自定义初始资金与原费用字段、账户记忆隔离、旧历史分页/权限/失败与API严格金额/未来日线/单位契约。既有完成账户历史测试仅删除已消失本地tab state的硬编码第12槽，不删除任何断言。

一次定向自审：使用`git -c core.autocrlf=false diff --no-index`核对本包精确变更；用`node`与已安装TypeScript AST核对11个业务读取/写入/校验函数原文不变。未扩大到全仓审查。未执行TSC、完整构建、原PG/HTTP或本人视觉，不把组件测试当真实系统链路。

## 保护、风险、成本与下一步

字节备份`logs/stock-replay-pages-18-backup/`，修改前SHA `logs/stock-replay-pages-18-before.json`；最终3文件SHA `logs/stock-replay-pages-18-accepted-sha.json`；包含新测试的精确差异`logs/stock-replay-pages-18-final.patch`。回退只按对应hunk撤回；若文件有后续修改，不整体覆盖。无DB迁移，无新增依赖/外部模型/数据费用；K线按页面有条件挂载，金融方法/输入版本/原结果不变。

剩余风险：当前源码未发布，真实账号导航、账户恢复、窄屏布局与浏览器滚动尚未验收。人工路径：获准恢复当前预览后，以股票身份依次进入交易/持仓/订单/资产，确认URL、标题、主内容一致且三个账本无K线/下单/推进；切换两个本人回放账户确认无旧账本残留；创建自定义资金账户核对金额；在已结束账户查询完整历史；模拟撤单超时后确认新增页内链接及账户选择锁定，显式原请求重试成功后恢复。

下一轮3包：根统一TSC/全前端与当前源码构建；获准原服务/HTTP与本人四页验收；独立评估既有跨页面离开后的未确认写入恢复（不能借本包改共享导航或假称已持久化）。
