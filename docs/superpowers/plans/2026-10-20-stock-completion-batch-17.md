# B17 股票盘后逐证券缺口 Implementation Plan

**2026-10-20 23:26检查点：** 四包实现并通过本机验证；后端2646通过/3既有skip/734warnings、前端496通过/TSC0、真实私有PG60检查通过且清理、974保护零漂移。本人及独立复查缺口已补；真实只读核对19日13缺/19未知未解决，不把展示/诊断算作数据补齐。未发布/浏览器/人工验收。详见`docs/STOCK-COMPLETION-BATCH-17-VERIFICATION-2026-10-20.md`。

> 使用executing-plans、TDD、systematic-debugging、verification-before-completion；本轮顺序实施四包，不提交/建分支，不改期货/共享生产逻辑。

**Goal:** 把盘后快照的缺行情/未匹配目录代码列表变成可分页、搜索、追溯的只读诊断，不用猜停牌或补假价处理缺口。

**Architecture:** 股票现有router新增GET `/api/stock/market/eod-gaps`→typed Query→股票独立service校验既存快照版本和覆盖集合→当前目录注释→当页证券在快照日及以前的股票历史行数。原boards/members/screen、worker、账户及数据源不改。当前目录注释不是快照时点目录；未知证券后来入目录仍保留原未知分类。筛选指纹绑定快照及目录注释和条件，翻页变化409。

**Tech Stack:** 现有FastAPI/Pydantic/SQLAlchemy、React/Zod、pytest/Node test、专用PG5433真实JWT/ASGI。

## B17-1 边界、保护与后端契约
- [x] 保存B16保护verify及B17收紧白名单基线；复用完整生产迁移/执行/bridge保护，不覆盖旧清单。只允许股票stock_market.py新增端点、新stock_eod_gaps schema/service及新股票客户端/组件、覆盖面板入口和测试。
- [x] 先红后绿：现有SQL/HTTP fixture验证snapshot hash/批次元数据/计数与缺口集合、未来/失败批次排除、未知后来入目录不被升级、分页搜索/稳定顺序/条件指纹、无snapshot/disabled不当零缺口认证。
- [x] GET kind=all|missing|unknown、keyword<=64、limit1—100、offset0—20000、expected_fingerprint可选64hex；无上游调用、8秒、no-store/request_id；坏快照/DB失败503明确错误，指纹变化409，股票权限沿现有router。

## B17-2 前端严格契约
- [x] 新stock-eod-gaps-api.ts及测试，捕获JWT只读GET/no-store/20秒/取消，校验状态、分页、来源/日期/单位/版本/合法整数、禁用与无快照区分；非法条件不请求，坏/旧响应不回退假成功。

## B17-3 完整股票UI
- [x] 新stock-eod-gap-browser.tsx及行为测试，现覆盖面板显式打开；分类/代码名称草稿→应用/重置/分页/重试、真实null与0、当前目录/历史说明及来源限制，指纹翻页和409重新应用提示。
- [x] 身份/市场/JWT盘/登录代次变化立即取消清空，晚到拒绝，同key往返失效；只展示已在当前目录的证券详情链接，未知不自动当可交易或补采。

## B17-4 真实验证与回顾
- [x] 新私有PG/HTTP测试：真实批次/目录/历史、非法及权限、hash坏、变更409、公共行情/期货fixture账户/订单/流水不变；fixture清理，不写public、不执行来源/模型。
- [x] 逐包自查与独立只读复查、受影响及完整后端/Node/TSC、最终保护verify；有条件运行既有只读生产数据诊断，不将其当独立来源认证。
- [x] 保存真实文件/命令/红绿/失败与未执行、金融口径、无DDL回退/成本/风险/人工路径及下3—5包。未部署/浏览器/人工验收不称整体完成；1:00/6:10既有续做保留、不重复新建。

## 回退及禁止事项

无迁移/回填；仅新增股票端点和可收起入口。退回该入口和新增端点/模块即可，不回退前批金融/权限修复，不改共享layout/main或期货。缺口诊断只说明记录事实，不能据缺价判断停牌/退市、把后来目录替代历史成分、混qfq/原价、自动开交易或无预算补采。后续数据版本/DSL/基准/运行发布仍按独立子计划推进。
