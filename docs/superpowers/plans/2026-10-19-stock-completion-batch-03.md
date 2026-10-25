# 第三批：分盘行情读取与股票价值因子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将第二批安全关闭的部分只读能力用真实分盘数据重新接通，同时修复股票价值因子的确定性排序错误。

**Architecture:** 保留现有工具注册/ExecutionContext/研究任务框架。仅三项独立工作包；不改变模拟账本、不开放聊天写、不迁移或重导行情。虚拟行情缺失明确报错，不经新浪或live缓存补价；旧虚拟底图seed不当作真实虚拟成交序列。

**Tech Stack:** 现有FastAPI、SQLAlchemy、Redis、Pandas、pytest、TypeScript/Node。

**执行检查点（17:05）:** 三项子切片已实施；最终后端2419通过/3既有跳过，Node360通过，TSC退出0，实际PG/Redis/HTTP61通过且隔离数据清理。具体通过/失败/未执行见 `docs/STOCK-COMPLETION-BATCH-03-VERIFICATION-2026-10-19.md`。两项行情复查Important已红绿修复并二次复核；股票价值政策独立复查通过。未发布，不将父S05/R04勾为整体完成。

**实施中证据修正（必须续做保留）:** 读取现有producer后确认virtual namespace含新浪补价/pulse，旧source=virtual归档也未保留原tick来源。因此不能按最初“observed”字样承诺真实观测。本批报价明确拒绝这些来源；旧归档返回 `virtual_namespace_cache`、`archive_tick_origin_not_preserved` 和仅模拟展示用途限制。分盘读取可验收，真实逐笔来源认证不能验收；若需认证必须在后续改producer保留来源并分离pulse，不能在reader伪造认证。

## 已读代码与边界

- 前两批计划、真实检查点及主计划；已读工具quote_kline/indicator/registry/gateway/safety、market_keys/kline_keys、虚拟历史种子实现、股票catalog/engine/preprocess调用链、研究旧入口和snapshots。
- 既有virtual历史会自动从live渠道seed，本批聊天读取只消费独立namespace中的source=virtual归档及独立forming，不触发seed。种子保留在原图表业务中，不改用户历史数据。
- S04持久确认和P01发布DDL尚未完成；不以本批只读修复恢复聊天交易，不重启原主后端。R03五年容量、R02旧同步入口和不可变/PIT数据仍未完成。
- 不提交、不建分支、不读或输出密钥、不付费调用、不向公共用户账户写入。

## B03-1：报价和K线显式分盘读取（主执行者）

**Files:** `backend/app/services/ai/tools/market/quote_kline.py`、`tools/registry.py`、`tools/safety.py`、`services/ai/gateway.py`；新 `backend/tests/test_ai_read_market_scope.py`。

**契约:** quote/Kline工具增加keyword-only `trading_mode="live"`兼容原调用；认证registry显式传JWT盘，模型参数不能改盘。live优先现有market_keys的live键并兼容旧live键；virtual只读market:virtual与kline:virtual，不允许live fallback或上游补数。响应增加market/mode/read_at/来源范围，缓存时间未经认证不能宣称约1秒新鲜。

- [x] 先红：真实Redis键替身同时放live=100/virtual=500，测试quote/Kline均不串盘；只有live/seed时virtual明确缺数据且供应商调用0；模型伪造mode无效。
- [x] 校验行情是有限正价格，虚拟历史只用明确source=virtual的bar（仅归档标签，来源限制见上）；时间重复/逆序安全排序去重，冲突不无声覆盖；forming不得改写已收盘bar，当前quote不得覆盖旧历史bar。
- [x] 实现后只从MODE_UNAWARE移除本包真实适配工具，旧未适配研究/主力/盘口等保持关闭。
- [x] 目标测试→原行情工具回归→本批PG/HTTP/完整测试；不宣传真实上游全链认证。

## B03-2：指标继承分盘及数据来源（主执行者）

**Files:** `backend/app/services/ai/tools/indicator.py`、`tools/registry.py`、`tools/safety.py`；同目录新测试和工具设置已有测试。

**契约:** 指标工具新增keyword-only trading_mode，所有MA/EMA/MACD/RSI/BOLL/KDJ/VOLUME输入来自本盘K线；结果回传输入source/mode/时间/是否含forming，不改公式，不用模型计算。未知/缺数据返回明确错误。虚拟数据不足不读live补足。

- [x] 先红：相同symbol双盘不同价序列，MA数值不同且逐字段与确定性函数一致；禁用工具拒绝；缺virtual数据不触达live/provider。
- [x] 实施和API能力可见性同步，保留旧默认live调用兼容；原指标/聊天安全测试回归。
- [x] 真实PG JWT→SSE→gateway→registry→指标测试，行情使用明确合成隔离缓存，仅证明分盘，不作为真实市场认证；最终使用实际本地Redis独有QA键并清理。

## B03-3：股票PE/PB金融域政策与报告可追溯（独立分工）

**Files:** `backend/app/services/stock_factors/catalog.py`、`engine.py`、`METHOD.md`、`backend/app/api/stock_factors.py`的目录返回；研究schema/报告及相关测试如确需兼容增加字段可扩展。不得改root工具文件。

**金融政策:** PE≤0不具“低正PE价值”含义；PB≤0无法按正净资产估值，作为明确不合格样本排除，而不是横截面中位数填回可选池。非有限数也不能入样。合法正值正常排序；成长/收益率负值并非同一政策，不可一律删负数。市值无效只对需市值的中性化/加权采用现有明确拒绝，不补造。

- [x] 写反例并运行red：负/零/NaN/inf PE、PB，在fillna开关/MAD/Zscore/中性化后仍不进入选股或分组；正常正值与其他可为负因子不受误伤。
- [x] 规范化因子政策函数只由确定性代码实现；单因子与组合路径共同应用；规范化参数和预处理不能把金融域不合格恢复。
- [x] METHOD_VERSION升级（不覆盖旧报告），目录与结果保存policy/method版本/剔除规则/计数（必须能解释实际样本）；前端报告若新字段要显示，旧结果缺字段明确“未记录”。
- [x] 原金融/快照/任务schema/报告测试与真实隔离PG持久结果复开；父R04未覆盖的其他金融边界要列出，不能扩大称所有因子已正确。

## 汇总验收、回退与下一包

- [x] 新隔离PG/HTTP验证脚本明确namespace、JWT双盘、供应商/model成本0、业务数据不写公共schema、finally清理；本批未用原服务health代替功能验收。
- [x] 后端完整pytest、前端全Node/TSC，独立代码复查后修重要问题并重跑，记录真实red/green、失败/未执行。
- [x] 更新详细交付和主计划子切片进度，不把S04/S05/R04父包直接勾完成；不删除续做自动化。
- [x] 无表结构/迁移，无行情重导；新报告方法升级，旧报告按原保存值显示。虚拟新读能力出现异常应恢复关闭，不降级到live。发布受P01约束，未发布不称在线已修。

下一轮仍为：S04完整持久确认；P01安全发布DDL检查/迁移；R02旧研究入口归一；D01下一收盘缺口和恢复状态。R03按依赖加载/五年资源应在读版本契约明确后推进。
