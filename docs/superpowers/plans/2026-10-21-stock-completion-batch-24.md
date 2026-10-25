# B24 安全预览和 AI 实际验收 Implementation Plan

> 使用 writing-plans / executing-plans / TDD，当前会话顺序执行，不提交或建分支。

**Goal:** 提供本人账号可登录的真实数据预览，验证实际页面和一次短 AI 提交的非空回答，明确未开放能力和未计量的嵌入/工具子调用费用。

**Architecture:** 不修改任何已有生产代码、认证、网关、布局和运行配置。新增独立手动预览进程入口，复用原 create_app 路由和权限；用独立 lifespan 只做只读 schema readiness、PG/Redis 连接，不启动行情生成器、存量 AI/GP/研究队列、盘后补采和外部桥接。预览不是正式全服务发布，暂停能力显式503，不假装成功。

**Tech Stack:** 现有 FastAPI、SQLAlchemy、Redis、Next.js、pytest，无新依赖或数据库迁移。

## 边界和前置证据
- B23-final 1013 文件保护检查零漂移。当前 PG5433、其他进程3001在运行，不抢占或关闭它们。
- 当前 main.lifespan 已调用只读 verify_database_schema，不调用遗留 _init_tables；B23交付记录的“启动仍运行DDL”判断过时，须更正，不为此修改 main。
- 只读真实 PG 检查39表496列通过，仅表列存在检查，不是迁移/金融数据认证。
- 用户会在页面登录已有账号；禁止打印密码/密钥、抓取浏览器认证存储、伪造该用户JWT或冒用其账户。
- 股票 /api/ai/chat 明确409且全局助手隐藏。不能解除冻结的共享门禁，把期货工具借给股票；本轮先验证已有期货对话，股票独立只读 Agent 的持久幂等/上下文/预算另包。

## B24-1 独立手动预览运行切片
Files: 新增 backend/scripts/preview_service.py、backend/tests/test_manual_preview_service.py。
- [x] 先红测：无后台启动、预览身份清楚、任务/补采/外部执行入口503、普通认证/本人查询/股票回放不绕原权限；失败资源释放。
- [x] 新 lifespan 只读 schema 检查15秒、Redis单次2秒ping、PG engine echo关闭；不改env文件。所有服务仅127.0.0.1，原 API 仍认证。/preview/status 报告 manual-no-workers / 未认证全系统，带no-store。
- [x] 写操作精确白名单；GET/HEAD同样只允许核实的DB/缓存读取，新闻/期货K线外源兜底503，不以GET天然无副作用。真实原路由匿名401验证，不覆盖认证。既有迁移check=True校验后只打开EOD读取位，同步/catchup关闭；保留最近真实5549/5558快照，不启动采集。
- [x] 复用已有 local_service.spawn 隐藏进程机制；不改原 launcher，Frontend BACKEND_URL仅本进程。先核对端口，无监听才启动；仅核对命令行后重启本轮独立预览后端，不停止其他进程。预览uvicorn日志过滤query token/key等，历史本轮日志离线脱敏。

## B24-2 真实 HTTP 和浏览器预览
- [x] 真实 PG readiness、Redis ping、后端/前端HTTP、匿名受保护接口401、paused写503，保存不含凭据的证据。
- [x] 打开127.0.0.1:3000/login，用户自行登录已有账号；未抓取认证存储或伪造JWT。
- [x] 登录后已检查dashboard、行业六列/成分/个股K线周切换MACD/BOLL、研究覆盖、现存历史报告、本人模拟账户及委托历史入口。只读查看，未新建资金/委托、推进日期或触发研究；新任务因worker暂停而显式不可用。并非所有菜单的创建/运行/异常路径已验收。

## B24-3 AI 实际链路与复查
- [x] 已有账号期货助手/SenseNova模型一次UI短问题，实际非空返回“连接成功。”，未触发交易。原聊天仍有RAG/记忆/工具，不能声称一次UI提交等于一次上游请求；子调用数、token和金额未计量，不再增加模型测试调用。
- [x] 无模型列表/HTTP200替代实际回答；股票聊天仍关闭。费用闭环、工具能力及其他模型未认证；不修改冻结网关来伪装全功能。
- [x] 最终后端2741通过/3既有skip/741warnings、前端532通过/TSC0；新增预览10测覆盖清理/真实路由/读取位/脱敏。B23清单只有新增独立入口，无已有文件变化；B24保护1014文件零漂移。生产构建未执行。

## B24-4 检查点与人工交接
- [x] 保存记录 docs/STOCK-COMPLETION-BATCH-24-VERIFICATION-2026-10-21.md，含失败修复/真实命令/成本未知项/未执行验收；整体部分完成。
- [x] 预览回退仅停止经命令行/PID身份核验的本轮进程，删新入口不影响生产代码；无DDL，用户手动产生的业务数据不能靠代码回退删除。Redis数据不清空。
- [x] 下一四包：股票独立只读AI持久幂等与预算；历史PIT/锚点与五年缺价；DSL/真实基准和回放扩池；正式worker/构建/双市场发布回退验收。
