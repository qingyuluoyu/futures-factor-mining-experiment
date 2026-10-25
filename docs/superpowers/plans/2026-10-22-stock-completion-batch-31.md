# B31 股票原价归档版本目录 Implementation Plan

> **For agentic workers:** 使用executing-plans顺序执行四包、先红后绿；不提交/建分支，不覆盖B30及其他执行者。整个期货与共享认证/网关/布局/运行配置冻结。

**Goal:** 用户在股票数据覆盖页显式查看已有原价影子归档版本、日期/覆盖/来源口径及资格限制，避免只看最新worker状态就误认为五年历史/PIT/回放已就绪。
**Architecture:** 仅股票新增只读目录服务/Schema/router，由既有stock_market挂载；读取已有归档批次元数据，不扫描价量、不导入或变更研究/回放数据路径。分页固定snapshot_at高水位，单SQL count+page，条目逐项校验来源/版本/时点/覆盖。前端独立加载/重试，不阻塞原覆盖和缺口。
**Tech Stack:** 原FastAPI/Pydantic/SQLAlchemy/PG、React/Zod/Node；无新依赖或DDL。

## 契约和边界

GET `/api/stock/market/archive/versions`，需本人股票会话。limit1..20、offset0..10000；可选严格YYYY-MM-DD trade_date、带时区snapshot_at且不可未来。默认本次UTC时间并返回给后续分页；仅包含archived_at<=snapshot_at的批次，按trade_date/archived_at/id倒序。由于采集时间不是事务提交时间，同SQL对有序ID成员集计算32位hex membership_token（一致性标识，不是权限签名）；后续页必须同时带原snapshot_at和token，成员变化含旧时间晚提交返回409 snapshot_changed，等待显式刷新而非静默错页。无上游/模型调用、无用户/资金/任务写入。原archive_ready未就绪503，不伪造空成功；非法查询422，数据矛盾503含request_id、不泄漏原始证据。

返回contract_version、snapshot_at、items/total/has_more；每项只白名单batch_id/version/source_data_version/method_version/trade_date/source_collected_at/archived_at/counts/catalog_version及固定金融元数据。版本应等于既有content_hash(method_version,source_data_version)，row+missing=expected，source_rows=row+unknown，时点包含时区且交易日/采集/归档顺序和盘后窗口一致。不是实际逐行质量审计，所有条目明确metadata_only、research_ready=false/replay_ready=false，不能直接用于回测或下单；不输出source_evidence内部文本。保持Decimal原数据不变。

## 四工作包

- [x] P1 后端目录Schema/服务/原股票路由挂载。读模型与已有归档准备函数/权限/调用方，先写纯金融元数据和API异常反例，再实现。目录按请求快照单SQL，不读取observations或source_evidence。
- [x] P2 客户端契约与显式目录面板，挂在StockDataAvailability现有覆盖区；不打开则零目录请求。日期草稿显式应用、固定快照翻页/手动刷新重新锚定、错误不当0、登录/市场/revision隔离、来源和资格说明。先红后绿，原覆盖不受影响。
- [x] P3 私有真实PG/原ASGI：25批次元数据20+5分页、分页期间新时间插入不漂移，旧时间晚提交被成员摘要409拒绝；非法快照/字段矛盾、无价格表查询、权限/物理缺表，实际响应最终客户端解析；受影响和全回归、只读复查。不造真实价格、不调用上游、不改public。
- [x] P4 隔离build/TSC/保护hash、核对任务/PID后自有服务加载、本人页面只读/HTTP；记录实际文件命令、失败/未执行、金融口径、0新增直接源/模型、回退/后续四包。不存在public DDL；回退只还原声明股票挂载/入口及旧构建，保留原数据。

## 保护与验收

开工B30保护1051通过。只允许既有`backend/app/api/stock_market.py`、`frontend/src/components/market/stock-data-availability.tsx`及其测试改变，其余本包新增独立股票文件。先保存三源备份和原库存，再创建本轮保护清单。任何共享/期货变动另包兼容设计；不动原暂停AI、不绕供应商429。整站其余历史/PIT/五年/基准/Agent/发布人工验收仍未完成，目录不是这些能力的替代。

## 验证结论

2026-10-22本机四包验证完成，详见`docs/STOCK-COMPLETION-BATCH-31-VERIFICATION-2026-10-22.md`。最新全后端2824通过/3既有付费skip/757警告，前端646通过，真实私有PG33检查并清理、实际响应客户端解析4、TSC/build/HTTP9及本人目录4版本/显式刷新通过。独立复查发现晚提交分页P2，真实双连接先红后修，成员变化显式409，不声称跨请求持有PG MVCC事务快照。1062冻结无漂移，旧B30仅3股票源/测试改与9新增，两个原router挂载hunk逆去字节文本一致。无public DDL或新付费调用；3001部署b31-reviewed/26384，8002原main/33308，原AI paused/user。旧b31-final早于修复，弃用未部署；正式发布回退、完整用户及金融验收未完成，保留自动化。
