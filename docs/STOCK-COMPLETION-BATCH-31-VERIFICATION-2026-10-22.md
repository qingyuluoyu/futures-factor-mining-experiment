# B31 股票原价归档版本目录检查点

## 完成状态

本轮四工作包完成代码、受影响及全回归、真实私有PG/HTTP、实际响应客户端契约、本机构建和本人只读浏览器验证。新增股票筛选页的已有原价影子归档版本目录；没有将旧研究历史或回放切到新表，没有授予研究/撮合资格。全系统仍部分完成，不停止续做自动化。

本轮不改期货生产代码、共享认证/网关/布局/运行配置、B30收藏或B29提醒；无Git仓库、无提交/分支、新依赖或public数据库迁移。只允许两个既有股票生产文件和一个原股票测试变化，其余为独立新增。

## 修改文件与数据流

- 新`backend/app/schemas/stock_archive_catalog.py`：严格YYYY-MM-DD、带时区快照、limit1..20/offset0..10000、拒绝额外参数；后续页必须成对携带snapshot_at及32位hex membership_token。
- 新`backend/app/services/stock_archive_catalog.py`：既有StockDailyArchiveBatch元数据单CTE count+page；不读取observations/价格或source_evidence。白名单投影来源/单位/版本/时间/覆盖，校验方法与原content_hash关系、row+missing=expected、row+unknown=source_rows、字段完整性/盘后窗口/先采集后归档/非未来。逐项标记metadata_only_not_row_or_PIT_certification、research_ready=false、replay_ready=false，异常不变空成功。
- 新`backend/app/api/stock_archive_catalog.py`：在原stock_market router仅新增import/include两行挂载GET `/api/stock/market/archive/versions`。沿原股票市场和登录权限、请求编号与日志；8秒超时，503未就绪/校验失败，422参数，409目录变化，所有路径no-store及nosniff。元数据为已授权行情的共享目录，不读取任何用户账本/持仓或研究私有结果；匿名401、期货409。
- 新`frontend/src/lib/stock-archive-catalog-api.ts`及测试：严格响应、数量/分页/时点/资格/重复校验，双快照字段回执匹配，12秒超时、不重试、不把503当0。源/方法/授权口径按原记录，不读取或发送任何密钥。
- 新`frontend/src/components/market/stock-archive-catalog.tsx`及测试；原`stock-data-availability.tsx`及测试：明确点击后才挂载目录，独立加载，不阻断旧覆盖/缺口/股票列表；日期显式应用、单页20、上一/下一页、刷新重新锚定；身份/市场/token/revision/pending/卸载丢弃旧结果，成员变化隐藏失效页等待用户刷新。
- 新`backend/tests/test_stock_archive_catalog.py`、`scripts/verify-stock-archive-catalog-local.py`、`scripts/check-stock-b31-preservation.py`；子计划、本记录及主/冻结计划指针。

## 金融和一致性边界

未改变历史价量、Decimal、qfq/raw/复权锚点、金融公式、研究方法、交易日历或模拟撮合。目录披露CNY/Asia-Shanghai/日频/未复权/成交量手100股/金额CNY及Teajoin兼容来源；只检查元数据，不声称逐行真实性、交易日历连续、历史可得性/PIT、来源独立审计或再分发授权通过。

读取到已有4个版本：21日5548/5558缺10/未知20；20日5549/5558缺9/未知19；19日5545/5558缺13/未知19；16日5548/5558缺10/未知19。它们不是连续五年行情或全市场完整研究池。22日清晨未收盘，本轮没有调用来源或生成22日收盘价。

初稿只用archived_at高水位，独立复查发现正常归档事务可能先写时间、首屏后才提交：同一cutoff会悄然增加成员。当前同一SQL对筛选后有序UUID成员集计算MD5 membership_token；分页成对带原时间/标识，成员不一致专门409，不自动重试或用新集合续旧页。**token只检测成员一致，不承载权限，不是签名、数据真实性证明或跨HTTP持有的PG事务快照**。当前归档只追加不更新日期/排序字段；未来若支持更新/修订，需扩展摘要/契约版本后再支持，不把本包当通用可变列表快照。

## 实际命令与验证

根目录：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_archive_catalog.py backend/tests/test_stock_market_overview.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-stock-archive-catalog-local.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q -rs
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b31-preservation.py verify logs/futures-preservation-b31-start.json
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-ux-runtime.py
```

frontend目录：

```powershell
$files=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName)
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files
node node_modules/typescript/bin/tsc --noEmit --incremental false
$env:WENYUAN_BUILD_DIR='.next-stock-b31-reviewed'
$env:WENYUAN_PREVIEW_BUILD='1'
$env:NEXT_PUBLIC_CHAT_SAME_ORIGIN='1'
$env:BACKEND_URL='http://127.0.0.1:8002'
npm run build
```

- 目标后端20通过，`logs/stock-b31-backend-target.log`；成员修复后目录15通过，`stock-b31-backend-reviewed-target.log`。最新完整后端 **2824通过、3既有skip、757警告**，`stock-b31-backend-reviewed-full.log`；3项真实付费源测试未配TYPESAFE_TEST_KEY跳过，未新增/删除skip或改既有断言。更早首次全回归2824/3skip/758警告是修复前结果，不替代最终证据；警告为既有弃用和未await mock等。
- 最终前端 **646通过、0失败/跳过**，`stock-b31-node-final.log`。TSC exit0，`stock-b31-tsc-final.log`。最终build exit0，`stock-b31-build-reviewed.log`；旧b31-final在成员修复前构建，弃用未部署。Next两次自动include已移除，再由原writeConfigurationDefaults恢复v3和Windows尾换行，tsconfig原SHA256 `05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250`一致。
- 私有真实PG **33检查通过并清理**，`stock-b31-private-pg.log/json`：25批次20+5分页、首屏后新时间append旧页仍25、双连接未提交旧时间行→首屏→commit→旧页409、显式刷新、单SQL/无价格表读取或证据输出、日期、边界、认证、期货、未就绪、物理表缺失503/恢复、坏口径/缺字段拒绝及修复、public归档/研究/AI摘要不变。夹具仅元数据且标记QA，没有价格、成交或真实研究；使用真实原ASGI/PG服务和fixture auth，不冒充生产JWT写验收。随机私有schema已清理；物理表rename/恢复只发生在私有schema，public无DDL。
- 3份私有PG/ASGI及1份public只读真实目录响应通过最终客户端解析，`stock-b31-real-client.log`：4通过、网络0。这是离线fetch适配的契约验证，不冒充浏览器；本人浏览器真实读取另外完成。
- 原main运行HTTP **9检查通过**，`stock-b31-runtime-final.log`；不是性能/容量或完整系统认证。

## 真实失败、自审和复查

新服务/API缺失14红后绿；缺失data_available_at被默认为null和历史cutoff后的日期错误分类两项红后修，`stock-b31-validation-red.log`。客户端/显式入口红`stock-b31-client-entry-red.log`、目录组件缺失红`stock-b31-ui-red.log`后目标绿。

自审发现时间字符串格式错误时Zod refinement会先toISOString抛RangeError而非返回Schema failure；`stock-b31-timestamp-red.log`真实反例后加有限时间守卫，最终全回归包含该项。

独立只读审查发现上述晚提交P2，根代理两条真实PG连接复现：未提交行首屏total25、提交后旧页原来返回200并变成员，`stock-b31-late-commit-red.log/json`的late_commit断言false、清理true。先保存红证据，再加同SQL成员摘要和409，真实PG最终33绿；前端契约/组件双字段反例`stock-b31-membership-client-red.log`后绿，并加409不重试/手动刷新清锚点回归。

独立复查确认P2闭合，另跑后端15/前端三文件16通过；无DB内存审计核对PG方言SQL编译、query配对、service变成员异常、ASGI409及请求号/no-store头。没有将主代理实库/全量/build结果说成独立验证。两次apply_patch因长行匹配不足被工具整体拒绝，拆成精确hunk后成功，无部分写入或覆盖。

## 期货保护与运行

开工B30原1051保护通过。B31最终 **1062冻结文件零增删改**，`stock-b31-preservation-final.log`。原B30库存另算只有3声明既有股票源/测试改变、9声明新增，`stock-b31-source-audit.json`；源码挂载逆去两行与旧备份文本一致。三文件修改前字节副本`logs/stock-b31-before-*.bin`与原B30库存SHA一致，备份及当前源最终复验，`stock-b31-backups.json/source-audit.json`；没有把允许列表当所有代码未改的证据。

实际重启前只读确认研究/归档/付费AI/主播活动0，原1AI任务paused/user，`stock-b31-preflight.json`。核对自有8002监听31848、启动父2300、PID文件和精确full_local_backend命令后仅加载本包股票路由；当前父19136/监听33308。自有3001原37852核对后换26384，使用`.next-stock-b31-reviewed`；旧b30-final保留。PG/Redis/Sina未停止，不恢复收费任务，不改运行配置文件。原main研究/EOD/归档worker true、GP false不变，release_verified=false。

## 人工只读预览、成本与回退

本人已有股票会话进入3001 `/market/screener` → 查看数据覆盖资格 → 查看原价归档版本目录，实际显示4版本及21日缺10/未知20/真实source+catalog版本；点击刷新快照时间更新、总数仍4。没有创建任务、资金/订单或修改收藏，没有读取JWT/localStorage/密钥。`logs/stock-b31-catalog-preview.png`及`stock-b31-catalog-detail-preview.png`保存警告、数目和真实版本同屏证据；窄预览视口原布局不在本包重构。

没有新增直接Teajoin/付费模型调用。原后台/页面的新闻行情语音间接费用未独立量化。原普通AI请求仍provider_rate_limited、cost_state=unknown；没有重试，不能宣称AI对话恢复。本包API/验证工具不写public业务数据，无public迁移/回填；原后台自身状态写未全局计量，不把局部只读校验当整站零副作用承诺。

无新数据库结构，无迁移回滚。回退先核对source-audit当前SHA和后续执行者，以备份生成精确反向patch移除stock_market两行挂载和StockDataAvailability入口；保留原数据与独立新文件，切自有前端回`.next-stock-b30-final`、后端核对无活动任务再加载旧股票源。禁止为了回退删归档/恢复整库，不能强停他人或活动任务。正式发布、目标环境容量/安全、故障及回退演练、全部人工验收仍未执行。

人工复验：本人登录→A股→行情筛选→查看数据覆盖资格→查看原价归档版本目录。可留空看4版本或显式应用2026-10-20核对覆盖5549/5558；刷新重新锚定。真实生产只有4版本，浏览器无法测试20+5页/晚提交窗口；这两项由上述私有PG真实链路验证。不要把目录条目当五年历史或点击交易依据。

## 下一四包

1. 原授权数据缺口：复用已有来源证据/只读原因审计，补采前明确请求量/费用与授权；不盲重导，不以目录认证全部价格或PIT。
2. 股票只读Agent垂直链路：版本化工具Schema/白名单/引用/持久幂等/总预算/固定评测；模型429未解除不反复试，未完成入口保持关闭。
3. 可信研究与回放输入：历史/PIT/锚点、五年资源和基准资格、raw/qfq隔离及安全扩池，实际迁移/计算验收通过才切读路径。
4. 共享消息/音频及正式发布独立兼容包：market/mode/legacy_unknown、期货正负回归、容量/安全/回退和人工验收；不得借股票改动破坏期货或提前宣称全系统完成。
