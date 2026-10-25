# 股票原价日线影子归档 Implementation Plan

> 使用executing-plans / test-driven-development / verification-before-completion。dispatching-parallel-agents仅隔离模型/迁移与金融转换服务的文件所有权；无Git，不提交/分支。先读00:37交付与原总计划。

**Goal:** 为已有真实EOD快照提供股票专用Decimal、批次版本和可追溯影子归档，解决新数据落入共享整数手表会截断/混复权的问题，不伪装已经修好全部旧历史。

**Architecture:** 兼容扩展独立archive表，不改kline_history/期货/旧报告。内部CLI选定一个已完成EOD批次和预期版本，从数据库获取已有快照，严格hash/Schema/价量/标准标识/时点校验后一次事务写入完成批次与观察行；同来源+方法幂等并发不重复。无HTTP自动入口、不切换现有读路径、不在应用启动时迁移或归档。

**Tech Stack:** 原SQLAlchemy/PostgreSQL、Decimal/pytest，0新依赖/模型/来源请求。

## 包1：独立模型与版本迁移

新文件：`backend/app/models/stock_daily_archive.py`、`backend/migrations/20261019_004_stock_daily_archive.sql`、`backend/scripts/migrate_stock_daily_archive.py`、`backend/tests/test_stock_daily_archive_migration.py`。

契约：ArchiveBase；StockArchiveVersion(version/checksum)；StockDailyArchiveBatch(id UUID, version CHAR64 unique, source_batch_id UUID, source_data_version CHAR64, method_version VARCHAR64, trade_date Date, status VARCHAR16 completed, source_collected_at/archived_at timezone timestamps, row_count/expected_count/missing_count/unknown_count int, source_metadata JSON)。来源批次ID+方法唯一，id+日期唯一；rows1..10000、row+missing=expected、unknown0..10000。StockDailyObservation(batch_id UUID, instrument_id VARCHAR16, code CHAR6, exchange VARCHAR8, trade_date Date, currency CHAR3, price_adjustment VARCHAR8, volume_unit VARCHAR24, amount_unit VARCHAR8, open/high/low/close/pre_close/volume/amount Numeric38,18)。PK(batch_id,instrument_id)，unique(batch_id,code)，组合FK(batch_id,trade_date)→batch(id,trade_date) RESTRICT。股市/CNY/none/lot_100_shares/CNY限定，价>0且<1e20、量额>=0且<1e20、OHLC。所有字段non-null；JSON证据不冒充原响应存档。

- [x] 模型/DDL/迁移先红后绿；独立metadata不注册共享Base、不改旧版本，check校验结构/约束/索引/FK和SHA，事务/advisory锁/语句与锁超时，缺迁移只读check不补建。
- [x] 数值不因precision/scale被静默截断；服务拒绝超过Numeric38,18精度。应用层字段校验与DB有限值上界双重保护。

## 包2：确定性转换、事务归档和内部CLI

新文件：`backend/app/services/stock_daily_archive.py`、`backend/tests/test_stock_daily_archive.py`、`scripts/archive-stock-eod.py`。

`prepare_archive(source_batch, *, now: datetime) -> dict`：source_batch为已有EOD模型；返回batch字段和rows字段，可供SQLAlchemy insert。method_version=`stock-daily-archive-2026-10-19.v1`。source状态必须完成/部分完成、已结束且非未来/非未收盘当日；快照和列的日期/版本/方法匹配、全内容hash验证，固定市场币种频率时区复权/单位/授权来源。严格每行标识/交易所前缀、唯一/日期/OHLC/有限值，不补零/因子1；Decimal(str(numeric))保持原JSON数值，不重新宣称回到供应商原文精度。跳过明确unknown隔离19只，验证覆盖/缺口分区；只存known覆盖行，保留来源元数据和真实采集时刻，archived_at是现在而不是历史可得时点。

`archive_eod(session, source_batch, *, now) -> dict`：调用方事务，参数化有界insert，批次冲突返回同一version/行数，无UPDATE；异常回滚整批。直接接口不跨事务调用上游或修改原EOD/目录。CLI仅本地5433，必填source批次UUID和预期data_version，默认dry-run无写，显式--apply才写；30秒总超时、有限SQL超时，no model/provider。迁移未就绪拒绝apply，不隐式建表。JSON输出仅版本/数量/风险，不带密钥。

- [x] 数值/代码/日期/证据缺失/覆盖分母/未知隔离/未来数据/原hash变造/重复/事务/幂等先红后绿。审查加固幂等完整内容核对与拒绝交易日16:30前采集的午间快照。
- [x] CLI先dry-run，绝不下载或补齐585392旧qfq历史，不做raw与qfq无锚点直接比较。不暴露未完成用户入口。

## 包3：真实PG、安全应用与交付

新文件：`scripts/test-stock-daily-archive-local.py`。隔离schema真实PG验证创建/重复/check、并发幂等、小数手与大额精确回读、非法NaN/Inf/约束、失败回滚、未来/unknown不混入、公共旧历史/EOD/期货聚合与序列不变、清理；不以mock代替核心SQL。

- [x] 先隔离测试通过再本地公库兼容扩展迁移（空新表、不改旧结构），check通过；仅显式预期Oct16版本的原已存快照归档一批5548行，不改用户读路径。
- [x] 专项85及后端2070回归、隔离PG21、本地公库归档/HTTP9通过；独立审查，无剩余Important；记录实际失败/修复/命令/影响、迁移顺序与前向修复，0外部费用。
- [x] 回退只停CLI/保留影子表，旧应用未依赖新表；已写影子数据不自动删除，不drop有数据表。因子观察/复权锚点完整历史迁移和发布验收仍下一包，不声称全系统完成。05:15先读本轮最新交付。

## 02:20实际检查点

交付`docs/STOCK-DAILY-ARCHIVE-VERIFICATION-2026-10-19.md`。004已应用本地5433，SHA b2f7c69d76d48c715d1572f9094a41563ecd7bf11cf607b26890cb332a729f07；归档batch f5ebaa36-febc-4789-a2aa-f4c265cf7ba5，version f372ea185838fcad514e95224a7d23e23a08b5aa4f39561e12b81cb8cdec6e1f。仅影子原价5548行，缺10/未知19不变，旧585392条/qfq/期货/网页读路径未改。85专项、2070后端（3既有skip/502warnings）、21PG、9真实归档/HTTP通过；0上游0模型。READ COMMITTED并发幂等成功，CLI同RR隔离并发允许40001明确回滚，不无限重试。未改UI不重跑前端或浏览器，不复用旧图冒充新证明。下一轮优先受控自动归档worker、005因子/锚点契约、数据缺口/真实收盘及AI/发布风险，不重复手动迁移或归档旧一批。
