# B25 固定盘后版本缺口对账 Implementation Plan

> 使用executing-plans、test-driven-development和verification-before-completion逐包执行；不提交或建分支。

**Goal:** 用已存的两个明确SHA256盘后版本解释缺口如何变化，为下一轮有预算补采提供证据，不将消失的缺口误判为已补齐。

**Architecture:** 新增股票独立确定性对账服务和本地只读CLI，复用原快照语义与归档校验。仅读取显式固定版本，不改latest选择、行情/研究/交易入口或正常运行服务。CLI是受信任数据库维护者本地工具，不是绕过用户身份的HTTP接口；结果落本机logs，禁止作为历史回测资格或独立真实性认证。

**Tech Stack:** 现有Python、SQLAlchemy/asyncpg、pytest；无依赖、生产DDL、上游或模型调用。

## 边界与前置证据

- 原1016保护清单本轮开工零漂移；无其他执行者。期货及共享认证/网关/布局/运行配置冻结，不重启现存服务，不改已授权手动暂停任务。
- 已存21日5548/5558、10缺20未知是来源事实，原因尚未证实。现有eod-gaps接口只列单日缺口，不能证明昨日缺口今日消失是补回行情、移出目录还是变为未知。
- 本轮白名单仅新增backend/app/services/stock_eod_reconciliation.py、backend/tests/test_stock_eod_reconciliation.py、backend/tests/test_stock_eod_reconciliation_cli.py、scripts/audit-stock-eod-reconciliation.py、scripts/test-stock-eod-reconciliation-postgres.py及本计划/验证记录和主计划检查点链接；不修改原生产文件。
- 禁止新采、补零、填因子、重写快照、自动更新证券目录；当前目录只作“现在”注释，不代替历史目录，不推断停牌/退市/新股/供应商失败原因。

## 包1：确定性固定版本差异契约

- [x] 先红后绿；最终29个新增测试，含错误元数据及原异常文字泄露反例，未弱化断言。
- [x] 新服务build_reconciliation(previous,current,catalog,now)：复用validate_gap_snapshot及prepare_archive验证，输出显式before/after成员状态、missing/unknown集合变化、当前目录注释及哈希匹配、固定方法/内容hash。
- [x] 结果标注仅保存来源对账、非独立认证、非PIT/回测资格、非交易日连续证明；原快照和参数不修改。只读独立审查发现1条Minor，先红后修并复核闭合。

## 包2：有界只读操作入口

- [x] 新read_reconciliation读取两个固定SHA版本及最多10000条当前目录，版本缺失/重复/顺序冲突不回退latest；所有列表有上限。
- [x] CLI必填--previous/--current；仅专用本地PG5433；REPEATABLE READ READ ONLY、语句8秒/锁2秒、整体30秒，不重试。新文件独占写入logs，失败只记录安全错误类型，不打印连接或原始证据。
- [x] 真实隔离PG14检查通过：固定版本、真实写SQL拒绝25006、同事务目录稳定、新事务变化、异常拒绝/public摘要不变，schema已清理；不是生产JWT验收。

## 包3：真实已有数据核验与复查

- [x] 读取已存20/21日版本实际运行CLI两次，30行对账内容hash一致；旧9缺/19未知全部延续，新增601326缺价、920296未知；没有额外请求Teajoin或模型。
- [x] 109受影响测试、PG14、原服务HTTP6和1018扩展保护通过；旧1016仅新增新股票服务/audit CLI两文件，无旧文件改删。最终完整后端2773通过/3既有skip/741warnings，真实日志见验证记录，不冒用旧计数。
- [x] 真实记录docs/STOCK-COMPLETION-BATCH-25-VERIFICATION-2026-10-21.md含失败、金融口径、成本/回退和后续三包；原服务未重启，股票AI未开放、系统仍部分完成。

## 回退与人工验证

无数据库迁移或业务写入，回退仅停止使用新CLI/模块，保留证据文件；不能删除旧真实快照。人工在终端使用明确两SHA运行CLI，检查before/after及目录hash；页面继续使用现有缺口浏览器。此包不宣称新增页面或股票AI已可用。
