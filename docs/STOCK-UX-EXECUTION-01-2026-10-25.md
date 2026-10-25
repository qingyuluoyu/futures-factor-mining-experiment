# 登录背景与tick股票图表第一批交付记录

**已实现待验证**。按`docs/superpowers/plans/2026-10-24-stock-visible-workspace-remediation.md`执行P0–P3四包；源码接线、目标测试、构建和保护核验已执行，真实服务/本人浏览器验收尚未执行，不能称整个系统或课程已完成。

## 变更与数据流

1. 新`frontend/src/components/dashboard/dashboard-frame.tsx`为无状态展示骨架。`frontend/src/app/(main)/dashboard/page.tsx`仅把原期货工作台的Card结构提取到该组件，原市场分发、用户/盘key、ActiveChannelBadge/AssetSummaryBar/WatchlistTable/PositionOverview/RecentTrades/AiTradingOverview引用保留。该处是计划明确的展示兼容例外，不能声称此文件“完全没动”；没有改期货交易或账户业务。
2. `frontend/src/components/landing/auth/login-dashboard-background.tsx`复用DashboardFrame，不再另画公开数据看板。匿名背景显示同一工作台卡片结构，自选/账户/持仓/成交为登录后查看，不伪造私有值；现有公开任务汇总的无凭据/8秒/取消/错误行为保留，登录表单和建立会话代码未修改。当前背景复用的是正式期货工作台结构；股票工作台仍有独立市场看板，因此不宣称未登录背景能自动知道账号所属市场并与每个市场逐像素一致。
3. 新`frontend/src/components/market/tick/echarts-candlestick.tsx`直接派生本地tick的完整ECharts组件，保留主副图、联动光标、数据缩放、标记/价格线和图表信息栏实现。版权/完整MIT声明见同目录`THIRD_PARTY.md`，参考工程未修改。新`adapter.ts`把现有股票指标结果映射tick字段，`theme.ts`接现有display/CSS，不引入另一套主题存储。
4. `frontend/src/components/market/stock-kline-chart.tsx`保留原props、股票偏好、日周月/指标/量能/放大/复位控制和金融口径说明，内部画布改为动态加载tick。股票行情详情和回放调用自然进入新图，无需改API。控制数组和量能配置稳定化，避免悬停时重复生成整套配置。副图与主图高度按tick布局/可用窗口适配，不再使用旧的280+104×副图数强制堆叠；实际窄屏滚动、切股/缩放/主题等交互仍需浏览器验收。

实际修改：上述3个既有展示/股票文件，`frontend/package.json`、`pnpm-lock.yaml`，两个既有前端测试。新增DashboardFrame及测试/测试加载器，tick组件/适配器/主题/测试和来源说明。计划、AGENTS和master仅记录指针。没有改共享认证、网关、全局布局、运行配置或期货K线/交易引擎。

金融口径：仍用当前股票stock-chart-data的方法版本；十进制小数成交量不取整、不缺量补0；原价日线涨跌使用供应商参考前收，缺参考不填0，原价周/月跨除权不输出伪收益；禁用参考代码无依据的“至今”收益。没有增加分时/分钟/异动数据或把其伪造成可用，没有接真实资金。

## 验证与失败记录

前端工作目录`frontend`，Python为项目根`backend/.venv/Scripts/python.exe -X utf8`。

```text
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/dashboard/dashboard-frame.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/dashboard/dashboard-frame.test.mjs src/components/landing/auth/login-dashboard-background.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/tick/adapter.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-kline-reference.test.mjs src/components/market/tick/adapter.test.mjs src/components/market/stock-quote-detail.test.mjs
node node_modules/typescript/bin/tsc --noEmit
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test <Get-ChildItem src -Recurse -Filter '*.test.mjs'得到的全部路径>
pnpm build
python -m pytest backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
python scripts/check-stock-b33-preservation.py verify logs/stock-ux-final-inventory.json
```

先红后绿证据：`logs/stock-ux-frame-red.log`、`stock-ux-tick-red.log`、`stock-ux-entry-red.log`、`stock-ux-modular-red.log`。首次骨架测试缺jsx空props导致测试夹具错误，修正后22登录/展示目标通过，`stock-ux-login-final.log`。适配器首次相对TS导入不能被Node测试loader解析，改为已有alias；真实ECharts SVG与指标适配3通过，`stock-ux-tick-second.log`。图表/入口/详情18通过，`stock-ux-entry-first.log`。最终29目标包含股票/期货市场分发与精确原期货Card HTML对照，`stock-ux-final-targets.log`。

全部前端**693通过、0失败、0跳过**，`stock-ux-frontend-final.log`；后端市场/盘隔离**11通过**，`stock-ux-market-regression.log`。这不是全期货实库验收。类型检查exit0，`stock-ux-modular-tsc-fixed.log`。

初次完整引擎构建通过；模块化后构建失败，`stock-ux-build-final.log`：core EChartsType与完整入口ECharts私有字段声明冲突。用`ReturnType<typeof echarts.init>`修正，没有as any/关闭检查。最终源码构建**exit0**，`stock-ux-build-accepted.log`；输出独立`.next-stock-ux-20261024`，没有替换或启动旧预览服务。Next自动改写tsconfig include，已精确还原到本轮前SHA，不把构建副作用留进共享配置。

真实SVG由移植组件构造ECharts option渲染，图像`logs/stock-ux-tick-layout.png`，已实际查看主图和四个副图。该图标题明确OFFLINE LAYOUT QA / SYNTHETIC TEST DATA，数据仅来自测试脚本`stock-ux-preview.mjs`，不进入应用，不能当真实证券数据或本人浏览器验收。首次图片输出路径URL编码错误，修为fileURLToPath后exit0，`stock-ux-preview-final.log`。

## 依赖、保护、兼容与成本

唯一新直接依赖ECharts=5.6.0，与本地tick锁一致；新增解析包只有echarts5.6.0、zrender5.6.1、tslib2.3.0，旧package/snapshot版本和内容无变化，`logs/stock-ux-dependency-delta.json`。安装首命令不支持fetch-timeout参数失败；改用限定网络配置并ignore-scripts后成功，`stock-ux-echarts-install-retry.log`，未更新旧依赖。

官方[安全公告](https://security.apache.org/projects/echarts/)列出6.1.0前Lines系列提示框的CVE-2026-45249。本模块只注册candlestick/line/bar/scatter/custom及所需组件，不注册受影响的地理Lines系列，不接收任意外部ECharts option；输入日期/数值来自现有校验适配器。保留该范围判断，不声称库本身无漏洞或全供应链扫描通过；未来升级须独立兼容包。

UI修改前清单1134包含执行11及执行12五个在建保护文件；本轮7个声明旧文件变化，其他1127 SHA不变。字节备份`stock-ux-20261024-backups.json`、参考测试的原SHA验证备份`stock-ux-reference-original.bin`、精确声明差异`stock-ux-declared-changes.diff`、保护断言`stock-ux-preservation-final.json`。新最终清单1141：`logs/stock-ux-final-inventory.json`，原guard verify通过，`stock-ux-final-guard.log`。旧baseline不覆盖，不据中间构建配置回退用户修改。

未执行迁移015–018、外部行情/模型或付费AI；新增付费调用和外部费用0，仅本地构建/测试及npm下载。五个原端口3001/8002/8110/5433/6380本轮均未监听，未重启。未执行在线HTTP、真实行情/账号浏览器、权限实库、线上交互性能或发布回退验收；未称全系统完整。

回退：股票图表切回该文件的字节备份，移除新增动态图表引用；工作台恢复原精确展示JSX；登录背景恢复其原文件；依赖只撤本次3个新增包，保留原版本和其他用户修改。没有DB回退。独立构建保留为证据，不删除交易数据、不停止其他进程。

人工路径：服务获允许运行后，未登录观察背景与正式工作台结构→本人登录→A股板块/成分股/个股→主图及副图/日周月/缩放/悬停/切股/浅深色/全屏→缺价缺量错误不伪造→期货工作台/交易/任务和市场切换回归。公共背景不展示未认证用户私有数据。

下一四包：本人页面视觉和交互验收；A股其它页面按期货成熟交互统一并保留股票字段；自定义因子接检验/回测/报告；原历史数据/基准/执行约束与复现，再接必做LightGBM。当前执行12的Parquet下载失败和未验证源码保留，优先级已由用户调整，不能自动忽略本UI验收继续扩底层。
