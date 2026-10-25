# 夜间续做真实检查点（2026-10-19 23:50，北京时间）

## 状态与范围

本轮3包：N01登录凭据客户端安全、N02超级因子结果读取反馈、N03研究任务取消归属与槽释放验证。代码与自动化/隔离验证通过；实际浏览器验收未执行，系统整体仍部分完成。没有宣称GP连续演化、可信期货历史、完整期货研究、A股AI或生产发布已完成。

已核对本线程执行者，仅root运行，无其他执行者同文件开工；读取主计划/最新品牌与聊天/B05检查点，未覆盖之前修改。既有业务使用次数不限策略保留：因子/回测不按日次数限制，A股研究无累计1000门槛；并发、合法输入、质量门、权限、幂等及外部成本保护不取消。

## N01 凭据安全

独立登录页和首页登录弹窗原来都将密码明文读写localStorage。现统一使用 `loadRememberedAccount/saveRememberedAccount`，只保存用户名及开关；旧 `qihuo_login_password` 在进入任一登录入口时只删除、不读取值，不保存/备份秘密。UI改为“记住用户名”，密码仅在本次用户提交中发往既有登录API，原生浏览器密码管理可继续使用。

SSR不访问浏览器存储；存储禁用/异常时降级为不记住，不阻塞普通登录。没有改服务器密码/hash、token有效期或会话撤销；S06服务端撤销/刷新轮换仍未完成。没有读取、清空或记录任何真实用户的浏览器密码；已登录且未进入登录入口的其他设备不会被宣称已清理。浏览器禁止存储时也不能保证物理删除成功，只保证程序不读写密码值。

## N02 超级因子真实反馈

`useTaskChampions` 按taskId记录结果/加载/错误；切任务立即返回空的待加载状态，旧请求清理失效后不能覆盖新任务。失败不再当成空成功，显式重试只发一次读取，不自动无限重试。

详情现在分别显示加载、读取失败及重新读取、成功但没有合格因子、或真实保存结果表格；切换任务不能显示上一任务的冠军。移除“2000年至今全量历史”和“支持断点续训”承诺，明确历史覆盖依赖数据、非交易时段现worker暂停，核心种群演化/固定输入快照/可验证恢复仍在修复。

本轮没有修改GP算法、原始行情、质量阈值或后台市场/盘权限，因此不能说“超级因子现在已能正常挖掘”；现有创建/恢复算法仍有B05已记录缺陷，未通过加代数、假冠军或放宽金融标准掩盖。

## N03 取消归属与队列

此前完整回归偶发失败，是测试固定用owner服务取消首个被认领任务。服务按created_at/id排序，创建时间相同则UUID次序可使另一个owner先被认领。现用固定时间与两种UUID次序建立确定反例：保留旧假定时other先认领稳定404；修为由实际被认领owner取消，并新增错误owner/登录盘404且任务仍running/token不变、取消后计算槽保留、错误worker不能释放、匹配worker确认才释放、下个任务才可认领的断言。

未改生产研究服务、排序、权限或状态机，没有放宽测试关键断言。真实PG/HTTP对两种顺序复验，验证的是服务边界和持久状态，不是假装启动/杀死计算进程。

## 实际文件

- 新 `frontend/src/lib/remembered-account.ts`、`remembered-account.test.mjs`
- `frontend/src/app/(auth)/login/page.tsx`
- `frontend/src/components/landing/auth/auth-login-form.tsx`
- `frontend/src/components/super-factor/use-mining-tasks.ts`、`super-factor-page.tsx`
- 新 `frontend/src/components/super-factor/mining-feedback.test.mjs`
- `backend/tests/test_stock_research_job_service.py`（只改测试，双顺序及更强权限/槽断言）
- 新 `scripts/test-night-safety-postgres.py`；B02 harness仅report_label增加night。
- 本记录、详细子计划及主计划检查点。无生产模型/DDL/数据迁移，无新依赖、提交或分支。

## 真实命令及结果

1. 前端目录 `node --test src/lib/remembered-account.test.mjs src/components/super-factor/mining-feedback.test.mjs`：初始6反例失败（含新helper未实现），`logs/night-safety-red.log`；实施后通过。追加取消不实挖掘能力文案反例先红后绿，`logs/night-mining-claims-red.log`。VM跨realm Error最初导致返回通用错误，测试显式提供宿主Error后验证真实错误字符串，不改产品异常边界迎合测试。
2. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_job_service.py -q`：构造确定UUID初次全数字hex在SQLite被数值转换导致夹具错误；改含字母UUID后，旧假定稳定1 failed/8 passed（other先认领），`logs/night-queue-order-red.log`。不是权限放松或生产排序改动。
3. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_job_service.py backend/tests/test_unlimited_usage_policy.py -q`：**20 passed、40 warnings**，`logs/night-queue-green.log`。
4. `backend/.venv/Scripts/python.exe -X utf8 scripts/test-night-safety-postgres.py`：首次本地PG/Redis/backend/frontend均无监听，ConnectionRefused，未建QA schema，报告 `logs/stock-completion-night-pg-20261019-154719.json`。随后通过现有 `./scripts/run-local.ps1 -TimeoutSeconds 60` 恢复五服务，工具返回ready，未强杀进程或修改公开数据。
5. 同PG命令恢复后：**66检查通过，隔离schema清理true，0受控上游/付费调用/公共业务写**，`logs/stock-completion-night-pg-20261019-154835.json`。真实JWT/用户、API依赖、PG事务/行锁、owner/mode错误404、匿名401、trace、槽保留/释放及原账户余额订单流水不变。外部模型为既有harness明确传输夹具，不认证真实模型或行情。
6. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q --junitxml=logs/night-safety-backend.xml`：**2471 passed、3既有skipped、626 warnings，117.36秒，退出0**，`logs/night-safety-backend.log`。未删除或跳过测试。
7. 前端目录 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)`：最终**428 passed、0 failed/skip**，`logs/night-safety-node-final.log`。
8. 前端目录 `node node_modules/typescript/bin/tsc --noEmit --incremental false`：**退出0**，`logs/night-safety-tsc-final.log`。
9. 本地独立HTTP健康/公共HTML冒烟：backend `/health`、frontend `/login`、`/` 均200，登录HTML包含“记住用户名”且无“记住密码”，`logs/night-safety-http.json`。它只证明资源与新入口加载，不认证业务全链路。
10. IAB `getTab(http://localhost:3000/login)` 被浏览器安全策略拒绝获取页面观察权限；未尝试换浏览器、CDP、其他页面或其他间接方式绕过。没有浏览器截图或实际登录/挖掘详情交互验收；独立HTTP只是服务器测试，不当作浏览器验收替代。

## 迁移、金融口径、成本、回退

没有生产数据库变更，不需要DB迁移；真实测试只用随机私有schema并清理。行情/复权/费用/资金/公式/质量门和研究方法版本不变，取消原有业务次数配额策略不回退。

本轮受控验证0新增付费/供应商调用，没有重导数据、创建公共任务或使用用户模型密钥。服务恢复后既有后台调度的成本不包括在“受控验证0调用”中，不声称整个进程零外呼。

回退前端可分别撤回本批反馈/偏好UI；**不要恢复旧明文存密码代码**。旧密码键的删除不备份/不可恢复，也不应恢复；用户应输入自己的密码或使用浏览器原生密码管理。无服务端密码更改、无账户迁移。只回退测试/QA脚本不改变生产服务行为。已保留之前只读启动门，不能恢复遗留破坏性启动DDL。

## 续做安排

通过应用automation工具在本对话新建并核对两个ACTIVE heartbeat：`a-04-40`（每日北京时间04:40）、`a-09-50`（每日09:50），首个下次运行日为2026-10-20。既有02:05/07:10/12:10/15:30/20:33未擅自更改；不复制新线程，不要求模型无限调用。仅有实质变化/失败/完成/需决定通知，无变化安静。

未达到核心端到端、关键金融权限、发布回退及人工验收全通过，因此不停止续做，也不标整个系统完成。

## 人工路径与下一四包

人工：进入独立登录页/首页登录弹窗，应显示“记住用户名”，不会由程序回填旧密码；正常输入自己的密码登录，市场盘绑定保持。期货 `/history` 超级因子→已完成任务：读取错误应显示失败和重试，不是“无冠军”；切任务不可闪现他人任务结果。不要在原GP核心未修复前反复加代数。

下一轮四包：①不可变行情输入清单与版本化GP种群/RNG/best_seen检查点；②原子认领、暂停取消/重启恢复与市场/盘隔离的worker切片；③S04持久服务端确认/同事务幂等及P01版本迁移；④可信期货数据审计与独立期货研究契约/计算/持久化入口。各包先读直接代码再补细计划，不把UI反馈修复当算法或数据授权修复。
