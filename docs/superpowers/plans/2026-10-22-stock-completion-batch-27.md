# B27 A股行情与财务响应完整性 Implementation Plan

**Goal:** 阻止个股详情串证券、异常日期/未来财务与矛盾板块统计进入A股页面。
**Architecture:** 沿用现有股票API及Zod，增加消费边界校验，不修改后端金融计算或期货；保留负PE/亏损/零成交和null语义。
**Tech Stack:** 既有TS/Node、真实只读PG+ASGI、Next独立构建；无新依赖/DDL/来源或模型调用。

## 四工作包
- [ ] 1. 先红后绿：详情标准证券标识与本次code匹配、真实ISO日期、覆盖区间、估值/公告截止和重复披露校验；负金融指标/零/null不误删。仅stock-market-api及其测试。
- [ ] 2. 先红后绿：行情日期、coverage层级、行业样本计数、唯一行业/证券、成员价格/量/状态一致性和响应数量边界。检查stock-screen与coverage调用方，Zod扩展使用兼容safeExtend；不强行要求分页样本等于全池。
- [ ] 3. 新本地专用只读PG/ASGI探针，保存真实boards/members/details/screen，原股票权限和错误头验证；Node解析实际返回，不用mock代替真实数据库。公开业务写和来源/模型调用0，无伪造JWT或查看私人账户。
- [ ] 4. 全量Node/TSC、受影响后端和独立build、保护hash及必要本人只读浏览器，记录未执行/失败/成本/回退；不重启backend，不覆盖运行产物，不恢复AI任务，不报告全系统完成。

保护白名单：frontend/src/lib/stock-market-api.ts、stock-market-api.test.mjs、stock-screen-api.ts及新增股票审计CLI/保护脚本；其他生产源、共享认证/网关/布局/配置和期货严格冻结。保留B26最终1040保护记录，不覆盖旧基线。Next自动生成配置必须恢复原字节后验hash，不放宽检查。

兼容：旧qfq来源附加元数据可选；所有金融数值保留原单位和计算。异常响应显错而非空数据，部分失败由B26独立通道承接。无数据迁移，回退仅恢复股票消费文件和切旧前端产物，保留业务数据。供应商429与股票AI/历史PIT等旧未完成项不解禁。
