# B30 股票历史报告持久收藏垂直切片

**Goal:** 本人按股票研究任务收藏/取消收藏，并在历史报告页按原类型/状态筛选已收藏报告；不创建或改变研究结果/交易。
**Architecture:** 仅股票独立文件；使用已挂载stock_research_reports路由增加接口，不改main/共享认证/网关/布局/运行配置。新独立BookmarkBase与两张兼容表，不能污染原ResearchJobBase迁移元数据。收藏通过job_id外键归属原不可变用户/JWT盘，不冗余另一份owner；所有访问先走原ResearchJobService.owned/row。desired-state PUT+任务行锁序列化/主键幂等，GET无副作用。
**Tech Stack:** 原FastAPI/SQLAlchemy/PostgreSQL/React/Zod/Node测试，无依赖、模型/行情源调用或次数限制。

## 契约与安全

- GET `/api/stock/research/bookmarks/availability` → enabled/reason。研究原迁移未就绪沿原503；收藏自身未迁移返回enabled=false，旧backend404由新前端解释为未部署，不影响原报告。
- GET `/jobs/{job_id}/bookmark` → job_id/market=stock/bookmarked/created_at（未收藏null）。PUT同路径只接受严格bool `bookmarked`，拒绝extra字段；仅完成有版本报告可收藏，取消可重复。其他用户/盘统一404，期货409，事务/请求号/安全头/日志沿原execute。
- GET `/bookmarks?limit=20&offset=0&kind=&status=` → 原items/total/has_more；limit1..50、offset0..10000。一个SQL快照查询count+分页，按收藏时间/id倒序，禁止N+1或读取完整result；只返回本人当前盘完成报告。失败类型过滤返回真实0，不伪造报告。
- 收藏不是可信策略认证，原method/data_version/research_only保持；不开放原策略收藏菜单假装全模块接通。

## 四工作包

- [x] P1 后端Schema/独立模型/版本SQL/默认check迁移工具，服务幂等/权限/任务状态/单查询分页，沿原router安全封装与原迁移门禁。先红API反例，再实现；额外表不改变旧迁移结果。
- [x] P2 股票客户端契约、书签组件只读初始化/显式PUT/网络未知先查询不自动重写/身份旧结果丢弃，历史页“仅收藏”切换/状态组合分页/变更后刷新。旧backend能力缺失隐藏写入口，正常历史仍可用；先红后绿。
- [x] P3 真私有PG：迁移缺失/重复/结构错误/事务回退/并发desired-state、本人/盘/期货权限/原报告不变及单SQL分页，原响应客户端解析；全前端、全后端含期货回归、独立只读复查。无public测试订单账户。
- [x] P4 仅先private验证通过后应用public兼容迁移与check；确认无活动研究/自动收费AI、核对原main自有PID后加载新股票代码，保留paused/user。独立frontend构建/还原生成配置/保护hash、原main健康/匿名鉴权/本人只读UI。真实收藏写链路在私有PG验证，不为截图修改本人收藏。保存回退/风险/下一包。

## 迁移与回退

新增stock_report_bookmarks（job_id PK/FK RESTRICT，created_at TIMESTAMPTZ）及stock_report_bookmark_schema_versions；只新增，无原表ALTER/回填/删字段。DDL事务、5秒锁/30秒语句、独立advisory lock，checksum和结构/FK验证，默认只check、显式apply，loopback5433安全门禁。记录依赖原研究表但不锁表扫描历史；新表为空，失败事务回滚。回退旧股票源/旧前端，保留兼容新表和version，不DROP数据；无Git一键回退假设。public迁移版本标记/新增表属真实公开库变更，不称整轮零写。

冻结整个期货、所有B29共享例外本轮也重新冻结。仅股票明确白名单代码可改，新旧文件预先hash。模型/源预算0，不动用户资金/订单/原结果或恢复收费任务。系统其他未完成包继续，不能将报告收藏当作策略收藏/股票AI完成。

## 本机验收结论

2026-10-22：四包本机验证及预览加载完成，详细证据见`docs/STOCK-COMPLETION-BATCH-30-VERIFICATION-2026-10-22.md`。全前端635/全后端2809通过（3项既有真实付费调用未配Key而跳过，757警告）、私有真实PG51、真实响应客户端解析3、TSC/独立构建/HTTP9通过；独立复查迟到onChanged旧筛选竞态先红后绿。public兼容新增两表/版本已apply+check，原研究任务/AI任务摘要相同，新收藏0；本人页面读取与筛选通过，未代用户保存收藏。1051冻结文件无漂移，原B29库存仅5个声明股票文件改变和9个声明新增。3001使用.next-stock-b30-final，8002原main及暂停AI任务保持。正式发布/回退及用户持久写人工验收未执行，整站仍部分完成，保留自动化。
