# B32 固定原价版本逐证券对账 Implementation Plan

**2026-10-23运行约束更新：用户主动关闭项目并要求“暂时不要重启”。** 本计划P4运行/本人验收待用户明确允许后继续，禁止按旧恢复记录自动启动。安全离线整改继续，见`docs/STOCK-RUNTIME-HOLD-2026-10-23.md`；不恢复付费AI。

> **For agentic workers:** 使用executing-plans顺序执行四工作包和TDD。不提交/创建分支，冻结期货及共享认证/模型网关/布局/配置，不覆盖B31。

**Goal:** 在已有归档版本目录显式核对一个固定版本和一个标准证券标识，读取真实原价/小数成交量及明确缺失原因；从只看批次元数据推进到已存来源与影子观察全量逐行一致性核验。
**Architecture:** 复用现有stock_raw_panel，不重新实现金融校验、不抓源。原股票归档router增加GET，单请求仅1个版本/1只股票，内部原全量核验最多10000观察、3条查询；CPU校验移到线程，原返回保持。stock独立Schema/客户端/面板，查询前不读，身份/市场/版本/取消隔离。无DDL、用户或交易写入。
**Tech Stack:** 原FastAPI/Pydantic/SQLAlchemy/PG、React/Zod/Node。无新依赖/模型调用。

## 契约与安全

GET `/api/stock/market/archive/versions/{version}/observation?instrument_id=000001.SZ`，version为64位小写SHA，标准证券6位+SH/SZ/BJ按交易所匹配，拒绝裸码/未知参数。股票登录权限、原archive_ready门禁、request_id/no-store/nosniff；未知固定版本404，不读最新；来源/价量不一致409，未就绪/DB503，8秒超时504；每worker最多2个核验、超额立即429不排队/不自动重试。保留单次资源边界，不添加每日次数。

返回原stock-raw-panel-v1 manifest，只含1版本/1证券、1批次和至多1条观察或明确missing分区。Decimal以原字符串输出，前端以精确十进制校验OHLC/非负量，不浮点截断或补0。返回source_calls/model_calls=0、同源已存一致而非独立真实性/PIT/历史连续/回测/撮合认证；不输出source_evidence或原始响应内容。旧qfq研究/模拟注册不切换。

## 四工作包

- [x] P1 后端Schema/路由与已有读取器最小兼容修改。先红：严格输入/未知版本类型/线程卸载/异常与并发；再实现。stock_raw_panel新增ValueError子类用于未知版本，不以错误文本当协议；原3查询与结果保持，CPU build仅await to_thread。复查补充线程内非阻塞BoundedSemaphore(2)，HTTP取消后仍限制实际CPU执行，finally释放。
- [x] P2 客户端与显式逐证券对账组件：固定版本选择→手填标准ID→提交→实际价量/缺失原因/来源及资格限制；目录刷新/翻页关闭旧面板，切版本/市场/用户/token/revision/卸载丢弃旧结果，未知失败不当空。先红后绿，不打开或未提交则零观察请求。组件/契约测试完成；本人浏览器验收留在P4。
- [x] P3 真隔离PG/原HTTP：复用原归档准备逻辑生成明确QA夹具，小数0.01手不丢、缺失/未知隔离、全量内容坏行/来源缺失拒绝、未知版本不读最新、只读/权限/资源；已存真实单版本只读核对、最终客户端解析，受影响和全回归、独立只读复查。不向public写假价量，不伪装独立认证。10月22日真实PG38检查、5实际响应解析；最终后端2829通过/3既有付费skip，前端655通过。
- [ ] P4 独立build/TSC/保护hash、核对任务/PID后自有服务加载及本人只读核对；保存实际命令、通过失败未执行、金融口径、迁移/回退、费用/风险/下一四包。本轮0新增直接来源/付费模型，不恢复原paused/user AI。正式发布与完整金融验收仍未完成。

## 白名单与回退

既有仅`backend/app/api/stock_archive_catalog.py`、`backend/app/services/stock_raw_panel.py`、`frontend/src/components/market/stock-archive-catalog.tsx`及其测试允许改变；其余新增独立stock文件。开工原1062保护通过，先备份与新保护清单。共享/期货不改、无数据库迁移，回退仅声明hunk/股票源及旧b31-reviewed构建，不删除原档案或恢复整库。CLI旧调用仍接受ValueError子类；不改旧manifest/方法/Decimal策略。

## 当前真实检查点（2026-10-23 02:07 上海）

**04:44更新：依赖和预览已恢复，覆盖下述不可达现状，但P4仍未全部完成。** 顺序完成三个运维子包：确认专用PG5433非运行而非默认系统PG，原pg_ctl启动后30420；原WSL keepalive恢复专用Redis6380；原main预检确认1个AI paused/user、无运行付费AI/主播/研究后加载自有8002；原前端加载`.next-stock-b32-reviewed`。最终HTTP9通过，真实隔离PG38重验通过，1070保护零漂移；`logs/stock-b32-runtime-restored-settled.log`、`logs/stock-b32-restored-private-pg.log`。未修改运行配置/生产源码，不恢复收费AI。首次前端因误用环境变量加载缺失的`.next`失败，进程已退出后使用原配置实际读取的WENYUAN_BUILD_DIR更正，仅进程环境调整；保留失败记录。当前3001/16804、8002/25444（父20956）；Sina8110仍未恢复，本人浏览器工具仍不可用，AI原429及正式发布验收未解除。详情见交付记录末尾恢复附录，不把历史不可达或旧PID当现在状态。

交付记录：`docs/STOCK-COMPLETION-BATCH-32-VERIFICATION-2026-10-22.md`。P4部分完成：TSC和独立`.next-stock-b32-reviewed`构建完成，1070保护文件无漂移；原四源码/备份SHA再次相同。续接复验47后端、655前端、TSC通过，但3001/8002直连ConnectError、PG5433/Redis6380 TCP超时。旧重启会话已不可恢复，不能据先前启动命令认定服务仍运行；未强停/启动未知服务。本轮浏览器工具不可用，本人预览/正式发布回退验收未执行，不标P4完成。先恢复并核验既有独立预览依赖和任务暂停状态，再按下述人工路径验收；不重复抓源/付费模型/数据重导。
