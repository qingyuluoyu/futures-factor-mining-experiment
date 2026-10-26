# 股票未确认写恢复安全补包（2026-10-26）

状态：**已实现待验证**。股票注册表、日线回放四种写入、自定义因子保存/停用已经接通明确登记回执，并通过本轮离线交互与全前端测试。原PG、HTTP、本人浏览器、发布构建/回退未执行，不能称②-B或整个系统验收完成。课程正式任务/内核仍由另一执行者推进，本包不重复造任务核、不覆盖其文件。

## 本轮四包及实际变化

1. 注册表无损登记：原实现超过8条丢最旧记录、序列化超限只留新记录、存储失败静默、同键可改业务内容。现在 `recordPendingWrite` 返回 `{ok:true}|{ok:false,reason}`；满载、损坏、读写失败、写后不能读回、同键不同意图均拒绝；不驱逐或覆盖旧未知结果。同键相同kind/signature/revision可继续重试，保留原创建时间。
2. 回放入口：创建账户、委托、推进交易日、撤单均在API前检查回执。登记拒绝时不发送，保留本页原参数/原键/原版本供用户显式重试，释放busy/in-flight；未发送的尝试不使当前读取失效。成功、确定拒绝和身份变化的旧处理保留；换页恢复不会自动重发。
3. 自定义因子入口：保存与显式确认停用同样先登记后请求。登记失败不走API错误清除分支；保留原命令，解除busy，不显示上次“保存成功”，不声称未落盘的命令已持久保存。精确版本、停用确认和用户/登录盘边界不变。
4. 整合与一次自审：补齐16KiB **UTF-8字节**边界（不是JS字符串长度），并纠正提示的上下文。注册表只能说明登记失败，不能断言之前HTTP是否发出：收藏调用方在未知响应之后登记，若通用消息说“尚未发送”会误导。通用消息现只描述登记，回放/因子在确认其本次API尚未调用的位置再说明“本次尝试尚未发送”；之前可能已发出的原请求仍需核对。

8条是单作用域的未确认记录资源边界，不是日/月业务次数或总次数配额。没有增加业务次数限制。

## 本包声明的六个源码/测试文件

- `frontend/src/lib/stock-pending-writes.ts`
- `frontend/src/lib/stock-pending-writes.test.mjs`
- `frontend/src/components/trading/stock-replay-workspace.tsx`
- `frontend/src/components/trading/stock-replay-pages.test.mjs`
- `frontend/src/components/factor-research/stock-factor-editor.tsx`
- `frontend/src/components/factor-research/stock-factor-editor.test.mjs`

字节备份：`logs/pending-safety-20261026-0610-backups.json`。仅六文件精确差异：`logs/pending-safety-20261026-0800-own.patch`；接受overlay：`logs/pending-safety-20261026-0800-accepted-overlay-sha.json`。不以此接受全仓或其他执行者的代码。

研究任务面板与收藏组件同期被另一执行者修改；本包只定向读取和回归其消费者，未写它们。当前任务面板已消费回执并核对签名；收藏仍为未知响应后登记，不能据本包声称收藏具备发前预登记或服务端幂等协议。旧研究hook未改。

## 真正运行的验证

工作目录为 `frontend/`，下列命令的输出在 `logs/pending-safety-20261026-0800-*.log`。

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/lib/stock-pending-writes.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 src/lib/stock-pending-writes.test.mjs src/components/trading/stock-replay-pages.test.mjs src/components/factor-research/stock-factor-editor.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 src/lib/stock-pending-writes.test.mjs src/components/trading/stock-replay-pages.test.mjs src/components/factor-research/stock-factor-editor.test.mjs src/components/factor-research/research-report-bookmark.test.mjs src/components/factor-research/stock-dataset-jobs.test.mjs
$allTests = @(rg --files src -g '*.test.mjs')
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 @allTests
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

- 原登记缺陷红：`...-0610-registry-red-accepted.log`，14项中7失败，非导入失败；首次中间红日志有测试辅助函数处理undefined问题，保留但不作为有效红证据。
- 字节边界红：`...-0800-byte-red.log`，新测试证明6000个中文字符被误当作16KiB以内并返回成功；修后绿。
- 自审消息红：`...-0800-context-red.log`，通用登记错误错误断言“尚未发送”；修后绿。
- 六文件定向：`...-0800-target-green.log`，**44通过，0失败/跳过**。
- 含并行消费者：`...-0800-consumers-green.log`，**66通过，0失败/跳过**。
- 最终全前端：`...-0800-frontend-final.log`，**894通过，0失败/跳过**；TSC最终退出0，`...-0800-tsc-final.log`为空正常。
- 测试调用真实注册表模块与可注入故障的Storage，检查API调用计数、8条原文保留、同键重试、owner/mode、确定拒绝、无自动重发。不把VM/内存Storage/注入API响应当原PG或真实服务验收。

仓库根目录实际命令：

```powershell
backend/.venv/Scripts/python.exe -X utf8 logs/verify-pending-safety-20261026.py
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-flow-20-observed-final-inventory.json
```

六份备份哈希核实、最终六文件overlay核实通过；18补充34项SHA均未变。**整仓guard失败**，相对20观测快照有20变化、0新增/删除：6项为本包，14项为其它股票任务/API/消费者和tsconfig差异，具体路径保留在overlay及`...-0800-global-guard.log`。20快照本来只是并发观测，不提升为全仓接受，也不为了消警重置任何基线。当前tsconfig SHA仍为 `71d9c7f89a19cb926d5386d52b962de49476fc9a72dfcea7ffa1b5477afc3a0b`，本包未写配置。

2026-10-26 08:07北京时间只读socket检查：3001/8002/8110/5433/6380均未监听。未启动服务或另开端口，未连接数据库、应用迁移或恢复paused/user AI。证据 `logs/pending-safety-20261026-0800-final-verification.json`。本包未执行git写命令；工作期间别的执行者可能提交，不能把git diff变少说成这些字节没改过。

## 金融口径、兼容和回退

金融公式/撮合/资金精度/费税/行情/数据资格/因子表达式均未改；仅保护发送之前的恢复信息与原键。数据和模型新增调用0，未安装依赖、未迁移。原localStorage键和五字段array格式保留，无服务端接口或库结构变化。新登记对非法revision、损坏/重复key旧数组及UTF-8超限严格拒绝；保留原文，不为让入口能点而清空未知结果。

回退必须先核当前六文件SHA与overlay相符；若已有人后续修改，逐hunk反向应用，禁止盲覆盖。可按备份恢复六文件的实现与测试，不需要数据库回退；但会重新引入旧的记录驱逐/静默失败缺陷，优先前向修复。已存在未确认记录不删除、不重写，旧服务端原键去重仍是权威。

## 仍未验收与人工路径

- localStorage同步读改写**不是跨标签事务**；并发标签可能相互覆盖。没有宣称跨窗口原子预留或持久化事务完成。后续若需要多标签强保证，应独立接有界锁/重载合并与真实多页测试，不只用本页busy替代。
- 旧read接口仍对损坏存储返回空数组；新写fail-closed，但旧只读横幅不能解释损坏。清除接口仍无成功回执，清除失败可留陈旧提示。不得自动丢失原键；后续以明确状态和本人核对处理。
- 回放API错误分支仍沿用旧的“全部4xx清除”规则，因子仅列举确定性400/401/403/404/409/422。本包未改变这些服务端错误契约；HTTP408等代理超时能否证明未执行尚未验证，下一恢复状态包须按真实错误类型收窄清除范围并补红绿，不把HTTP状态本身一概当未执行证明。
- 本包没有新建课程IC/回测/ML正式输入，不修复缺历史，不认证5年/全市场，也不代表AI聊天、期货在线回归已经通过。
- 原服务、真实PG/HTTP/本人浏览器、真实多标签、发布构建/回退均未执行。本轮没有重跑构建，不引用执行20构建证明这六文件的生产产物已发布。

获准恢复原服务之后：本人登录A股→回放委托/推进及因子保存各一次；在本人测试浏览器拒绝本地存储，确认请求未发送且原键不变；恢复存储后显式重试，核服务端只产生一笔对应操作；模拟未知响应换页回来确认不自动重发；切用户/盘核私有命令不串；最后原期货只读/核心功能回归。不得拿本人真实请求随意造满8条或清空存储，满载/损坏场景先在隔离浏览器测试配置验证。

下一轮四包：①按owner接现有④-3正式任务派发与固定输入，不重造核；②课程IC/可成交回测/基准/原生ML正式UI与明确能力门禁；③只读恢复状态、清除回执和真实多标签边界；④获准原PG迁移/HTTP/本人/金融权限与发布回退验收，授权历史缺口按预算补采。未满足这些条件不报告全系统完整，也不停止续做。
