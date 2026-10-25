# 第四轮：股票历史报告查看、关闭与空页恢复

**已实现待验证**。沿第三轮下一包与U3继续股票研究→历史→报告的既有v1流程；本轮三个包为详情请求失败的显式恢复、关闭详情并阻止晚到结果重开、历史后页变空的原条件恢复。只改`frontend/src/components/factor-research/stock-research-history.tsx`及其`.test.mjs`，不改期货/共享框架/认证/后端/API/数据库/金融计算，不启用未完成v2或付费模型。

## 实现与边界

原详情最初GET失败时selected为空，只能回列表找原行重开；筛选/翻页后可能找不到该行。新增独立detailId保留当前查看目标，手动“重试报告详情”复用原受权限校验的读取链，不刷新列表、不改变筛选、不自动重试。400/401/403/404/422视为确定性参数/会话/权限/资源错误，清除选中目标和报告，不提供原请求重试；已有完成报告的可视化格式失败仍保留原导出兼容路径。前端处理不替代后端资源权限验证。

新增“关闭报告详情”只关闭本地查看：abort当前详情传输、增加requestVersion、清空选中任务/结果/错误/加载态。详情GET或结果GET即使在abort后返回也不能重开；不取消后台研究任务、不写库、不改变列表页码/类型/状态/收藏过滤或两份对照。原当前用户、token、sessionRevision、股票市场和scope检查保持，旧会话按钮不能再发详情请求。

后页空列表现在明确“当前页没有研究任务，结果可能已变化”，允许手动返回第一页保留原查询；只有第一页空列表使用“当前筛选条件下暂无研究任务”。不自动换条件、扩大范围或把后页空误判成所有任务不存在。原分页20/offset≤10000、收藏接口与其它功能门禁保持。

金融公式、方法版本、币种、单位、复权、数据可得时间均未改；旧报告/参数/导出/对照及数据持久化不变。没有新增业务次数限制、依赖、迁移或模型/数据调用。不能把这三个UI恢复包当作题目二固定数据、完整可成交回测或机器学习交付。

## 实际验证

修改前1144项保护校验通过；其他执行者查询只有root。先读股票历史组件/API契约/现有恢复导出测试及研究/交付子包，再保存字节备份并登记原可见页面计划的第四轮白名单。没有重写历史组件或另建报告实现。

前端目录执行：

```text
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/factor-research/stock-research-history.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test <Get-ChildItem src -Recurse -Filter '*.test.mjs'得到的全部路径>
node node_modules/typescript/bin/tsc --noEmit
```

先红：新增5个恢复用例失败，18既有通过（`logs/stock-ux-round4-red.log`）；实现后23目标通过（`stock-ux-round4-target.log`）。一次自审后补三项异常覆盖：确定性错误不重试、结果权限丢失清除元数据/导出动作、结果请求阶段关闭并丢弃晚到响应。最终**708全前端通过、0失败、0跳过**，`stock-ux-round4-frontend-all.log`；TSC **exit0**，`stock-ux-round4-tsc.log`。新增的8项测试均包含在最终708中。

这轮仅股票UI状态/恢复控制变化，没有新增依赖、路由或打包导入；按风险执行完整前端回归与TSC，**没有重跑生产构建/后端测试**，上一轮构建仅历史证据。不把VM交互或模拟传输晚到当真实PG/HTTP/本人浏览器验证。未执行金融样本、供源、AI对话、迁移及发布回退验收。

根目录实际保护命令：

```text
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-ux-round3-final-inventory.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py snapshot logs/stock-ux-round4-final-inventory.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-ux-round4-final-inventory.json
```

先独立逐项断言仅2个声明股票文件变化、无新增/删除，其他**1142旧SHA不变**，再生成新1144清单，最终verify通过。备份`logs/stock-ux-round4-backups.json`、精确patch差异`stock-ux-round4-changes.diff`、保护证据`stock-ux-round4-preservation.json`、最终日志`stock-ux-round4-final-guard.log`。无期货或共享配置改动。

只读监听检查`logs/stock-ux-round4-runtime.json`：原3001/8002/8110/5433/6380均未监听；没有重启服务/恢复paused/user AI/应用015–018。成本仅本地测试，外部数据和模型新增调用0。兼容性为本地增量按钮/状态，旧接口和持久结果保留，无数据库迁移。回退需核对当前SHA后只恢复两文件声明差异，不整体覆盖后续用户修改。

## 人工路径及下一包

获运行许可并恢复原服务后：A股历史→打开详情并在请求中关闭→确认列表/页码/对照保留且晚到结果不重开；模拟暂时失败→改变列表过滤→重试原报告；不可访问报告不提供原请求重试或旧导出动作；后页变空→返回第一页并核对过滤保留；旧报告可视化格式错误仍可依原兼容路径导出。本人路径未执行，整包仍待验证。

下一四包从已记录的业务断链继续，避免循环只改UI：自定义因子精确版本接固定数据依赖/IC及报告；真正Parquet引擎和有界数据任务/取消恢复（先读执行12HOLD，预算有限、不盲重试）；可成交回测与基准/复现/课程报告；在这些数据/金融依赖满足后落实必做本地LightGBM特征标签及排序全链路。服务和本人验收保持待许可，不能据旧启动记录自动重启。
