# 课程执行07：D4任务前端内部接线

**已实现待验证**。承接执行06，三个子包：固定任务读取/状态契约；有界幂等写入客户端；会话隔离的任务查看/确认创建/取消组件。仅新增股票文件，未挂生产页面/API，worker和Parquet未接通，015/016/017未应用。不能称D4整体完成、用户已经能建集，或LightGBM已经实现。

## 文件、入口与数据流

- `frontend/src/lib/stock-dataset-jobs-api.ts`及`.test.mjs`：Zod严格校验任务状态/流程/方法/结果、固定参数、范围/重复证券/来源与暖机日期；列表20默认100最大、校验分页与重复ID；详情与取消校验所选job_id。新建幂等UUID由调用方固定传入，不在重试时换键；规范化排序后核对返回参数，任务ID不错误等同幂等键。
- `frontend/src/components/factor-research/stock-dataset-jobs.tsx`及`.test.mjs`：接受token/scope及可选固定command；显示观察资格、明确待确认范围/池版本/幂等键，只有用户点“确认创建观察归档任务”且availability accepting=true才提交。没有自动建任务、自动轮询、失败重试或研究入口。关闭接收后已有pending/running仍可取消；取消确认更新所选记录。列表摘要不加载大参数，点击才读固定详情。
- 所有请求no-store、Bearer、12秒上限/AbortSignal；成功流式体上限1MiB、失败64KiB，非JSON/超大/状态错误不当空成功。原错误request_id和HTTP status保留，不回显未经验证的错误对象。失败提示保留原幂等键；中断fetch不宣称服务端任务已取消，必须收到cancelled回执才展示取消成功。
- token或scope变化立即隐藏旧数据，清理时中止旧请求；Identity对象和当前AbortController双重检查丢弃旧会话/旧操作迟到结果。不自行读写localStorage/JWT、不代用户登录。token不进入日志或缓存键存储。

组件未被原workspace引用，故未改变股票现有入口或期货/共享布局。命令由未来已认证父级构造，当前没有自由文本/模型输出执行路径；生产开放必须先完成执行器/迁移/来源/路由/权限与本人页面验收，不可直接挂组件伪装功能打通。

## 实际命令与结果

```text
node --experimental-strip-types --test frontend/src/lib/stock-dataset-jobs-api.test.mjs
node --experimental-strip-types --test frontend/src/components/factor-research/stock-dataset-jobs.test.mjs
node --experimental-strip-types --test frontend/src/lib/stock-dataset-jobs-api.test.mjs frontend/src/components/factor-research/stock-dataset-jobs.test.mjs
```

客户端初红模块不存在，`logs/stock-course-execution-07-client-red.log`；新增五项后通过，`...-client-first.log`。组件初稿先写后已删除，再以缺组件四项红重走实现，`...-panel-red.log`；合计9项目标通过，`...-target-first.log`。测试中修正过不合理前提：幂等键不是返回任务ID，选中ID一致性用于GET/cancel，提交只核对固定参数，不为同键硬编码job_id。

frontend目录实际执行：

```powershell
node node_modules/typescript/bin/tsc --noEmit
$tests = @(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object { $_.FullName })
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @tests
```

**TSC exit0；前端687通过，0失败/跳过/取消**，`...-tsc.log`、`...-frontend-final.log`。API测试mock fetch，组件测试使用既有VM hook夹具，不替代真实HTTP/浏览器/React渲染验收。

后端使用`backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_dataset_jobs_api.py backend/tests/test_stock_research_v2_jobs.py backend/tests/test_stock_research_v2_job_migration.py -q`，18通过，56条既有utcnow警告，exit0，`...-contract-regression.log`。本轮未改后端，执行06的236项是历史记录，不当本轮重新跑过。

原清单verify为四声明新增返回1，精确白名单断言1120旧保护SHA不变，无修改删除，`...-preservation.json`。新清单`logs/stock-course-execution-07-current-inventory.json`共1124，原guard verify exit0，`...-preservation-final.log`。命令为`python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-06-current-inventory.json`，随后snapshot/verify执行07新文件名，均使用上述venv Python；历史清单不覆盖。文档指针修改前字节备份`...-backups.json`。

## 口径、回退与未验证

未改金融公式、源数据或资格，completed只显示观察归档成功，不宣称可交易/可回测。客户端校验不能替代服务端权限/幂等/资格。业务次数不限；分页/超时/响应大小是资源边界。没有新增依赖、外部数据或模型调用，新增费用0，没有提交/建分支/迁移/账户写入。

服务沿执行06停机事实，本轮未重启、不重复通知。真实PG/运行HTTP/本人浏览器、完整生产构建、迁移/并发/发布回退、真实Parquet/五年性能/供源/ML未执行。不能以687测试替代这些验收。

当前无公开挂载，不改变旧API/结果。回退删除引用前先停止新任务接收、确认实际执行退出，再恢复精确股票挂载；保留兼容表和归档，不删数据、不停期货。当前只新增内部文件，生产无挂载需要回滚。

将来人工路径：本人登录所属股票盘→固定来源参数预览→确认创建→查看首次请求ID及固定参数→同键核对→取消且等待明确取消回执→切换用户/盘不见旧详情→损坏/断网显示真实错误。现在页面没有该入口，不要求用户测试未接通功能。

下一四包：D4真正Parquet依赖/分区；有界子进程worker及取消恢复；015/016私有PG迁移/并发与真实来源资格；任务API/组件独立配置Flag与生产入口完整接线。随后固定数据自定义IC、R3/R4及必做LightGBM。09:50自动化更新当前检查点/运行边界，保持原每日09:50/ACTIVE/线程，不创建重复。
