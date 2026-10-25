# B20 股票K线涨跌基准与小数成交量 Implementation Plan

**2026-10-21 04:57检查点：** 四包已实现并本机验证，未发布。后端2689通过/3既有skip/739warnings、最终Node510通过/TSC0、私有PG/JWT/HTTP52检查通过且清理；公开行情真实PG只读ASGI4检查通过（固定本地股票身份，非生产JWT认证）。原B20保护变化仅新3个Node测试及详情测试1项；扩展后自查再收紧raw缺量单位的一个测试hash，独立复核后最终990无漂移，原/扩展证据保留。原B19变化仅计划内股票生产5文件及详情测试，不修改期货/共享。0新增来源/产品模型/public业务写，无服务启停/构建/浏览器/发布验收。详见`docs/STOCK-COMPLETION-BATCH-20-VERIFICATION-2026-10-21.md`。

> 使用 systematic-debugging、executing-plans、TDD、verification-before-completion；顺序四包，完成后独立只读review。无Git/分支/提交，股票外代码冻结。

**Goal:** 修复股票详情和图表把不复权相邻收盘当官方昨收、小数手显示为零的问题，增加可追溯的响应口径验证；保持期货完全不变。

**Architecture:** 股票K线旧接口兼容新增pre_close及change_basis，旧eod_bars默认输出不变，只有股票K线调用显式要求参考价。前端新股票专用响应校验层复用原网络函数，不改包含共享市场切换的stock-api.ts；确定性涨跌助手供详情和图表共用，模拟回放显式不提供未认证涨跌基准。旧qfq不拼新原价，原始影子/历史数据不写入。

**Tech Stack:** 既有FastAPI/SQLAlchemy、React/TypeScript/Zod、Node test/pytest和本机PG5433；0来源/产品模型，无新依赖/DDL/服务启停。

## 已核实根因和边界
- `stock_eod.eod_bars`丢弃已存vendor_ex_rights_pre_close；`stock-quote-detail`与`stock-kline-chart`分别以相邻loaded close计算。100→除权昨收10→现价11时错误显示-89%，正确供应商参考涨幅为10%；只有一根raw日线也不应丢掉已提供昨收。
- 图表formatVolume低于一万用toFixed(0)，0.01手变0手；数据本身并非零。
- 原getStockKlineApi仅TS断言，详情现已有OHLC/重复日期检查，但不校验返回code/来源/复权单位/pre_close；不能把它说成完全没有校验。
- qfq历史优先和raw仅在无历史时fallback保持；不会把这批金融展示修正当成PIT/数据完整认证。原价周/月跨除权不计算可比涨幅；qfq仅标记相邻已载入复权K线变化，不假称官方当日幅度。
- 开工B19保护989通过；只允许backend/app/api/stock.py、backend/app/services/stock_eod.py、frontend/src/components/market/stock-quote-detail.tsx、stock-kline-chart.tsx及股票回放workspace一处图表基准参数和新增股票校验/计算文件。冻结stock-api.ts、期货、共享认证/网关/布局/运行配置和B19服务/归档。

## B20-1 兼容参考价服务契约
- [x] 先红：既有EOD fixture通过股票K线HTTP须保留pre_close=5与新增vendor基准；qfq响应不带raw参考价、不混两序列；非法ASCII代码及损坏参考价失败而非零。
- [x] eod_bars添加默认false兼容keyword include_reference；新股票调用true，raw响应change_basis=vendor_ex_rights_pre_close，qfq=adjacent_loaded_adjusted_close；既有直接调用返回结构不变。
- [x] 所有调用方/现有测试复查，只读数据库，无迁移或原始记录重写。

## B20-2 确定性计算和前端响应校验
- [x] 先红：raw除权参考/单根日线/缺参考不回退、qfq相邻变化、不可用模拟口径、非有限/除零、0.01手和极小非零量；计算仅引用当前参考或已载入前根，不读未来。
- [x] 新股票助手计算reference/change/change_pct及精度友好量文本；新客户端在原getStockKlineApi后Zod检查标准code、OHLC/唯一真实日期、提供时为有限正值的pre_close、单位/来源/复权/基准一致、数量上限和取消；明确兼容无metadata旧qfq，raw缺参考仅不可计算，不能误用邻收。

## B20-3 用户详情/图表完整接线
- [x] 详情header和chart共用同一基准，显示参考价来源说明；raw周/月和回放无参考显示不可计算，qfq标相邻复权K线变化，不误报官方行情。
- [x] 成交量和均量显示不把小数手归零，悬停保留原值及单位；复用现有tick风格图表和控制，不改布局/指标公式。
- [x] 行为测试用户入口、错误不保留旧报价、单根raw有参考显示涨幅、取消/原qfq兼容、回放明确unavailable；既有期货完全不导入新文件。

## B20-4 隔离验证和交付
- [x] 真实私有PG/JWT/HTTP：原价昨收/小数、qfq优先且不拼接、匿名401/期货409、坏源503脱敏、日期上限、public不变、清理；模型边界fixture不冒充付费调用。
- [x] 目标/完整后端、Node/TSC、保护哈希及独立只读复查；原B19清单保留5个本批修改文件的开工哈希，新B20原/扩展及最终允许文件hash独立保存，不覆盖原证据。
- [x] 保存文件/实际命令/红绿/失败/未执行/金融口径/无迁移及回退/成本/人工步骤/下一四包。无专用运行/浏览器/发布验收则只报本地验证，不报告整个系统完成或关闭续做。

## 回退与下一四包
新增响应字段可被旧调用方忽略，旧eod_bars默认字段不变；前端可单独撤回，旧归档/账本零写，无数据回退操作。金融错误修复不应通过恢复错误幅度作为默认回退，必要时关闭该幅度展示。后续四包：9/19原因和授权有界来源；PIT/历史锚点和五年缺价；可信版本DSL/基准研究；专用运行构建与双市场人工验收。共享安全/期货改动仍另包，不恢复次数限制。
