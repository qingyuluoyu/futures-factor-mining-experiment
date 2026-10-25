# 执行19：统一股票K线视窗、完整指标交互与股票入口恢复

**总体：部分完成。** 本轮五包源码已实现并通过离线定向/联合回归与构建，均为“已实现待验证”；没有发布、启动服务或完成本人/原PG/HTTP验收。课程正式任务、全市场可信历史和整体发布仍未闭合。

承接最新用户“去重复页面、所有股票K线/日线与tick指标、每日02:10/07:20”要求，按`2026-10-25-stock-chart-completion.md`执行。根协调两个原有子agent独占wrapper/目录，根独占renderer和整合；第三个重叠页面包因既有差异暂停重写，不把派发遇到账户使用上限当成功交付。仅股票白名单，原期货/共享代码不由本轮修改。

## 五个实际工作包

1. **股票统一工作区**：修改`frontend/src/components/market/stock-kline-chart.tsx`与直接测试，新增`stock-chart-window.ts`/测试。全部已有详情/回放调用方继续使用同一wrapper。日/周/月聚合、近60/120/250/全部、缩放/十字线/放大和原偏好保留；新增已载入日期输入、全部日期和明确空/非法窗口。日期只解析真实周期日期索引，并传可选`visibleDateRange`，完整数据继续计算指标。按bars引用/标的/复权/单位/涨跌参考绑定临时范围，换源当帧不继承旧范围；复位清日期回60，全部日期清范围改all。ResizeObserver改按真实有界DOM容器和控件高度测量320–680px，固定副图仍扩展画布、主图至少240px，保持纵向滚动。详情、行情/筛选复用入口与回放均消费这条链，不另造一份图表。
2. **tick完整现有指标目录**：新增`stock-chart-indicators.ts`/测试，扩展`tick/adapter.test.mjs`。六组为MA5/10/20/60、BOLL20/2上中下、VOL及VOL5/10、MACD12/26/9、RSI6/14/24、KDJ9/3/3；量比仍前1–20个同周期均量。全部六指标/价格成交量精简只修改六布尔偏好。逐线披露数量门槛/不足根数、零历史均量及无效量；MACD首价EMA初始化1根可算、34根仅参考，不承诺收敛。平价KDJ仍可为空，数量足够不代表数值/PIT/历史资格认证。沿现有`stock-chart-1.0.0`方法，不改BOLL样本标准差、Wilder RSI、EMA/KDJ定义或成交量单位、小数精度；不凭空增加源里没有的分时/分钟/异动。
3. **真实ECharts日期视窗与信息一致性**：修改`tick/echarts-candlestick.tsx`，新增`tick/date-window.test.mjs`。新可选参数只设dataZoom类别起止，所有x轴/19条现有series原输入不裁剪，选近几天不会重置EMA、删掉MA60前缀或重算VOL5/10/量比基准。首次/换源/换日期默认信息取窗口真实末日；重挂载不重放旧zoomRevision。一次交叉复查发现“手动平移到原范围外→悬停→切指标”副图被clamp回旧末日，已用真实native生命周期红例修复：只有负索引哨兵取默认末日，有效hover保持主副图同一日。date range是初始视窗，随后仍可手动平移已载入数据，不读回放游标后数据。
4. **恢复当前股票入口的最小断链**：首次全TSC发现既有`stock-dashboard.tsx` JSX回调缺闭合大括号；首次全前端826/831通过、5失败，四项来自`stock-board-browser.tsx`缺既有useEffect导入，一项旧`stock-replay-order-history.test.mjs`尚未加载现有pending-writes模块。只补两个生产单行hunk，历史测试消费真实pending模块及实际React懒初始化语义，原金融/权限/历史断言保留。未改回放业务函数、共享alias loader或数据库。两个生产文件反向撤单个hunk后与本轮观测前字节完全相同；没有借修复接受其余页面/资格代码。页面职责完整重写仍未实施。
5. **调度、保护和整合**：复用`a-02-05`/`a-07-10`，已工具更新并核实ACTIVE、每天北京时间02:10/07:20；没有新建重复自动化或停止未完任务。续做prompt已指向本检查点，保留安静通知/0费用/不自动启被关服务等边界。当前源码`.next-stock-chart19`生产构建通过但未部署。

## 实际验证与失败证据

frontend目录实际运行：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 src/components/market/tick/date-window.test.mjs src/components/market/tick/adapter.test.mjs src/components/market/stock-chart-data.test.mjs src/components/market/stock-chart-indicators.test.mjs src/components/market/stock-chart-window.test.mjs src/components/market/stock-kline-reference.test.mjs src/components/market/stock-chart-boundary.test.mjs
$allTests = @(rg --files src -g '*.test.mjs')
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 @allTests
node node_modules/typescript/bin/tsc --project ../logs/stock-chart-19-tsconfig.json
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

最终K线目标**53/53**（`logs/stock-chart-19-target-final.log`），股票入口恢复**46/46**（`...-recovery-final.log`），最终全前端**832/832、0失败/跳过，45.64秒**（`...-frontend-final.log`，实际文件列表同前缀`frontend-final-test-files.log`）。53与46包含在832内，不重复相加。真实ECharts SVG/全量原始series/日期类别和React effect注入生命周期链均实际运行，DOM尺寸受控节点不是本人视觉验收。原期货分发、身份、股票/用户/JWT盘/偏好、金融方法和来源客户端回归包含在全前端内，不能当后端全功能或在线期货验收。

定向隔离TSC与修后完整TSC均exit0，日志`...-tsc-isolated-final.log`/`...-tsc-final.log`。首次完整TSC失败保留`...-tsc-full.log`，stock-dashboard(49,3) TS1005；首次全前端5失败保留`...-frontend.log`。旧历史测试首修接模块后又暴露测试useState未实际调用懒初始化，保留`...-recovery-green.log`，没有放宽disabled=false断言，修正确React模拟后通过。renderer最初4红、旧zoom重放红、复查错日红及修后日志分别在`...-renderer-red/zoom-red/info-red-accepted/target-final.log`；事件大小写及新画布已替换的测试接线失败也保留，未拿这些错误当金融修复通过。

构建采用进程局部BACKEND_URL=http://127.0.0.1:8002、WENYUAN_BUILD_DIR=.next-stock-chart19、WENYUAN_PREVIEW_BUILD=1、RAYON_NUM_THREADS=2、NODE_OPTIONS=--max-old-space-size=2048，运行`node node_modules/next/dist/bin/next build`。**exit0、编译27秒、40页生成成功**，`logs/stock-chart-19-build.log`，ID `pW55dKRn_hqQkXCb6AVGn`。Next仅添加/重排生成类型include，已核其它配置相同后逐字节恢复，tsconfig SHA仍05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250；生成差异`...-tsconfig-generated.patch`，还原后再次完整TSC通过。未改/关闭lint/typecheck、未装依赖。

## 既有差异与保护：没有新整仓接受基线

开始前18 guard失败，发现**20个既有变化/8新增**，含股票页面/共享overview、pending-writes、数据资格020及**frontend/Dockerfile**；这不是本轮成果，来源尚未核实。逐字节备份在`logs/stock-chart-19-preexisting-backup/`，差异`...-preexisting-delta.json`，观测快照1190为`...-observed-preexisting-inventory.json`，均明确**observed_not_accepted**。020编号已被占用，不能另造同号或自动应用；Dockerfile保留但不背书其兼容。

本轮只接受12个股票文件的声明hunk，见`logs/stock-chart-19-accepted-overlay-sha.json`（7既有/5新增）。其中dashboard/board两个文件只接受上述最小恢复hunk，文件其余既有实现仍未整体接受。另10文件按本轮独占备份/精确patch记录。相对观测1190只有7声明变化+5新增，其余**1183字节不变**；28既有差异里26完全不动，两个只撤声明生产hunk后全文件与观测备份相同。证据`...-preexisting-preservation.json`/`...-preservation-summary.json`。34补充SHA全部与18相同。

最终1195快照`...-observed-final-inventory.json`只用于检测后续漂移，**不是整仓接受/数据认证**。相对正式18主清单1182，共25变化/13新增，其余1157不变，包含未认领的既有差异；不能以snapshot verify通过抹掉这些差异。继续以18正式完整接受+19声明overlay+未知差异单独核对。任何期货冻结承诺只覆盖本轮改动，不能声称既有Dockerfile变更已受期货回归认证。

字节备份与精确差异：wrapper `stock-chart-workspace-19-*`；目录`stock-chart-indicators-19-*`；renderer `stock-chart-19-renderer-before.bin`/backup.json/changes.patch；入口恢复`stock-chart-19-recovery-before-*.bin`/backups.json/changes.patch；root文档`stock-chart-19-root-before-*`。早期保护检查假定CRLF导致断言失败，已按实际LF修正检查算法，两个生产hunk的反向逐字节核验最终通过，没有放宽或重写未知源码。

## 未执行、兼容、费用与回退

2026-10-26 02:26只读查3001/8002/8110/5433/6380均无监听，`logs/stock-chart-19-runtime.json`。没有自动启动PG/Redis/前后端/connector/worker、应用迁移015–020或恢复paused/user付费AI；新增外部金融源/模型调用均0，不补采/导入tick263候选、不混raw/qfq/补0/伪造历史时间。API/数据库/方法版本/依赖不变；旧renderer调用不传日期仍兼容。新增每次展示的目录/视窗解析为本地有界线性处理，不增加外呼。

未执行：原PG、运行HTTP、本人浏览器滚动/全屏/实际桌面尺寸、当前新构建发布与运行回退、真实五年/全市场规则/ML正式任务验收。无本人证据，不称当前页面已生效或“整体可用”。观察到的未知pending-writes/资格020尚需独立金融/权限/恢复审查，测试成功不代表其正式发布门禁通过。

回退：先核各文件当前SHA，按本包hunk精确撤销，后续修改存在时禁止整文件覆盖；新增目录/窗口工具只有在wrapper引用一同撤销后才移除。保留原成功构建/全部历史数据、账户、未确认写入和结果；不DROP/重导。两个入口恢复只是语法与既有Hook导入，禁止为了“回退19”重新引入已知不可编译/运行错误；如需撤新功能，保留这两个缺陷修复。未知既有差异不属于本轮回退对象。静态源码回退方案已明确，真实发布回退仍未执行。

## 人工路径与下一四包

获新的明确运行许可后，先核当前源码/SHA与未知变更边界，再发布当前新构建：登录已有账号→行情板块→个股→全部六指标，滚到最后KDJ和页尾；选近5根仍核同日MA60/EMA/RSI/VOL10值不变；手动平移出初选范围、悬停、切指标确认主副图同日；复位60/全部日期/周月/同代码换源检查窗口重置；稀疏5根应逐线披露不足不补曲线；回放日期最大为游标、股/手与原始撮合口径不串；最后原期货入口/图表/交易/历史回归。未获运行许可不因定时自动拉起。

下一四包：①核实并分域接受/修复18之后未知20/8变更，重点Dockerfile兼容、pending写入保留/恢复与资格020安全，不重写已经存在的CRUD；②工作台仅摘要、行情板块/成分浏览、筛选多条件的余下分工，核现在实际overview实现而非沿旧计划重复造；③获准原PG/HTTP/本人滚动和新构建发布/回退验收，独立核tick候选来源、历史资格和明确预算下数据缺口；④兼容演进016业务任务/结果分区并打通固定数据自定义IC/可成交回测/基准/真实LightGBM正式任务与报告，保留旧v1/Flag和费用资源边界。只有全部核心和金融权限/发布回退/本人验收通过才称完整，不停止仍有未完成任务的续做。
