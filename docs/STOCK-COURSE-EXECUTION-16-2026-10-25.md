# 执行16：本人模型历史的登记、只读API与股票入口

**总体状态：部分完成。** 模型产物→私有登记→只读API→股票模型历史的代码链已接通并离线验证；019未应用，默认Flag关闭、原服务停，尚非用户线上验收或完整ML训练产品。保留执行15所有计算、原v1/期货及共享配置。

本轮三个子agent分域：019登记/迁移、模型只读API、股票client/library；根负责生产股票router挂载、边界回归、合并记录。共四个相关工作包，没有重建训练/排序/成交算法，没有新增训练POST、生产worker或基础设施。

## 文件和实际链路

1. **019兼容登记。** 新增 `backend/migrations/20261025_019_stock_ml_registry.sql`、`backend/app/models/stock_ml_registry.py`、`backend/app/schemas/stock_ml_registry.py`、`backend/app/services/stock_research_v2/model_registry.py`、`backend/scripts/migrate_stock_ml_registry.py`及两项测试。新增两张独立历史/版本表和Scope索引，最多5fold在一行事务登记；同owner/JWT盘幂等键同内容复用，异内容409。注册是内部有界生产者接口，先复用015归属/固定源校验，核实验冻结、模型/预测/时间/内容SHA；不信客户端自报资格，不认证holdout。
2. **只读API。** 新增 `backend/app/api/stock_ml_registry.py`、`backend/app/schemas/stock_ml_registry_api.py`、`backend/tests/test_stock_ml_registry_api.py`。路径 `/api/stock/research/v2/models` 仅GET availability、分页列表、ID详情、ID/report；复用现有JWT用户/盘、stock市场门禁、session与`app.state.stock_research_artifact_store`。默认 `stock_ml_registry_enabled is not True` 时availability明确false、其它503；不查询019、不创建存储、不导入native。没有注册/训练/评估POST。
3. **股票用户入口。** 新增 `frontend/src/lib/stock-ml-models-api.ts`、`frontend/src/components/factor-research/stock-model-library.tsx`及对应测试；修改股票专用 `stock-research-entry.tsx` / `.test.mjs`。研究/因子/回测股票入口可显式点击“查看本人模型历史”，才读取能力；不在普通页面初载查模型，不自动训练。列表→详情提供固定参数、实际UTC创建时间、知识截止、训练/验证/测试范围、模型/数据/特征版本、gain重要性和限制；JSON仅原始证据预览，不把它当完整可视化ML回测报告。
4. **安全挂载。** 根只在 `backend/app/api/stock_factors.py` 新增一处股票router导入与include；不动共享main/lifecycle/运行配置。新 `test_stock_ml_registry_mount.py` 验证真正父router下路径存在、默认关闭、GET-only、原研究/自定义路径保留、期货会话在业务访问前被拒绝。

子包说明分别为 `docs/STOCK-ML-REGISTRY-16-2026-10-25.md`、`docs/STOCK-ML-API-16-2026-10-25.md`、`docs/STOCK-ML-MODELS-UI-16-2026-10-25.md`。页面矩阵已追加本轮事实，历史“无ML API/UI”指完整训练/回测工作台，不能再误称没有模型历史代码。

## 数据、金融与权限边界

确定性金融公式、费用、标签/预处理/模型训练方法均未改。来源继续observation_only，模型是qa_only/research_only，`holdout_registry_verified=false`；没有仅凭sampleSHA认定独立未见测试，也没有GET触发评估/消费记录。

列表/详情批量join015元数据核本人/盘/版本/状态，不N+1重读全部行情、Parquet或导入LightGBM。显式报告先授权登记ID，再核内容SHA/大小及登记快照；交互预览超过8MiB读文件前409，元数据仍可查看，较大内部登记仍保持幂等。没有用空列表掩盖存储/结构/权限错误。

Zod与后端严格Schema、UTC/date/有限值、owner/盘/参数/分段约束保持。前端身份更换清旧开关与数据，nonce+Abort和直接auth store修订检查阻止父级渲染前旧响应回写；401/403/404清旧详情，临时错误只能显式恢复。后页空可回第一页，偏移上限明确。React开发双effect的初读取消恢复已补红修复。

**一次合并自审发现真实原生协议漏洞并修复：** 只验证SHA/Scope/runtime的恢复函数不证明文本可被LightGBM解析；把文本改为非模型、同步重算所有SHA/引用后，原019确实会接受。保留红例 `logs/stock-ml-registry-16-native-parser-red.log`。现在仅注册时实际构造Booster，核lambdarank目标、特征数/原生f000等列名、树/迭代数量和原生保存线程预算2；假文本、错列名、树数、threads8及regression目标均脱敏409、没有部分登记。list/get/report仍不新增native导入。

实测4.7.0的`Booster(model_str, params=...)`会忽略params；最终只传model_str并核实际保存参数，没有声称加载参数生效。这里只解析，不训练/预测，也不将它当正式任务硬资源验收。模型8MiB/最多5fold边界保留，正式生产者仍须进入已约定有界任务。

## 实际命令和结果

backend目录，原生用例仅进程局部设置绝对 `logs/stock-ml-qa` 与backend的PYTHONPATH，默认venv未安装ML/Arrow：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_ml_registry_mount.py tests/test_stock_market_capability.py tests/test_stock_research_api.py tests/test_stock_research_legacy_adapter.py tests/test_stock_custom_factors_api.py -q
.venv/Scripts/python.exe -X utf8 -m pytest <logs/stock-course-execution-16-backend-test-files.log列出的14个实际文件> -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_ml_registry_mount.py tests/test_stock_market_capability.py -q
```

- 父router初红3项，另一次测试直接访问FastAPI延迟包含router的`.methods`导致测试AttributeError；改为真实OpenAPI路径检查后仍3红（未挂载），实现后**52通过**，`...-mount-green.log`。没有放宽市场/旧路径断言。
- 首次联合后端**122通过、0失败/跳过，30.19秒，235条既有utcnow弃用警告**，`logs/stock-course-execution-16-backend.log`。随后原生协议红例补测与注册修复完成，最终019/migration/API/父挂载相关**21通过、0失败/跳过，11.59秒，63警告**，`logs/stock-ml-registry-16-native-parser-accepted-api.log`；旧122与21有重叠，不相加制造独立用例数，未虚称最后局部修复后重跑全部122。
- 默认无LightGBM/PyArrow/Narwhals环境探测及父router导入确认native均未加载，`...-default-import.log`；默认市场边界**11通过**，`...-default-boundary.log`。
- API使用真实两fold训练结果、实际019临时登记与ASGI，包含真实测试JWT本人/异用户/virtual-live/伪造header、冻结/无权、分页/UUID/Schema、隔离/损坏/超时/无DML与只读约束。测试库是原SQLite factory，**不是原PG或部署HTTP**。静态019/`--help`不连接原库。

前端目录实际全测试：

```powershell
$testFiles = @(rg --files src -g '*.test.mjs')
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 @testFiles
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

全前端**737通过、0失败/取消/跳过，38.06秒**，`logs/stock-course-execution-16-frontend.log`；实际清单 `...-frontend-test-files.log`。子包40目标和最终TSC退出0，`logs/stock-ml-history-ui-accepted.log` / `...-tsc-accepted-final.log`。真实ASGI四响应229,600字节由前端client完整解析，`logs/stock-ml-history-ui-contract-check.log`，不是只手写mock结构。未重复生产构建。

保留各包红/失败：019缺模块及约束、重hash fold自认证/缺完整日期、较大报告内部幂等、root原生伪模型均先红后绿；API曾联合33绿1红为019新增自审，修复后通过；旧019/前端final清单在最后修复前，续做采用下方root接受清单。未删除测试、skip或吞异常造通过。

## 迁移、兼容与回退

019是新增历史表，不改015–018、旧用户/账户/模型内容。runner默认只读check，显式apply也须先核015完整结构/checksum，019专用advisory事务锁、5秒lock与30秒statement超时、完整列/类型/default/PK/unique/check/FK/index反射通过后才登记checksum；不在启动时自动应用，不硬编码public，不绕用别的数据库。**原PG应用/并发与前向恢复尚未执行。**

此次只读模型router在代码挂载，默认Flag与未配置store均关闭；没有改变共享启动配置或开启能力。未完成迁移/存储/私有登记验证前，不能只设Flag true放行。回退为关闭模型历史Flag/撤一处股票挂载及专用前端入口，保留019数据、模型内容、原研究/任务；已写表不DROP，后续发现结构问题以前向修复处理。四份子包证据及根旧文件字节备份均保留，精确撤销不得覆盖后续用户修改。

**任务架构真实差距：** 当前016 SQL只允许kind=dataset、完成结果必须是观察DatasetRef、worker名亦限定dataset且运行≤300秒；它不是早期计划所称的通用研究/600秒ML任务表。不能把训练伪装dataset、删除检查或让旧数据worker误领取。正式训练/组合任务须独立兼容包，包含新旧Schema/读写/worker支持类型/结果引用/租约及旧runner反射兼容和回退；本轮没有擅自修改016或新增另一套未设计队列。

## 接受清单、成本与未执行

主清单 `logs/stock-course-execution-16-accepted-inventory.json` **1178**，补充 `...-accepted-supplemental-sha.json` **33**，guard通过。相对执行15的1167主项，仅声明股票`stock_factors.py`与股票研究入口/测试3项改变，**1164旧SHA不变**，新增11项主保护（含019 SQL/runner），无删除；原27补充SHA全部不变，加入6项SQL/runner/后端测试保护。两份清单有交集，不将计数相加冒称独立文件数。

期货及共享认证/网关/布局/运行配置未改。根备份 `...-backups.json`，分域最新证据 `logs/stock-ml-registry-16-native-parser-accepted-files.json`、`logs/stock-ml-history-ui-accepted-files.json`，不要使用它们自审前的final。旧native/Schema文件和root股票路由/入口的字节备份、精确patch均已保存。没有Git提交/分支或新依赖。

金融数据/付费模型新增调用及外部费用0；只有参数受控的QA本地计算与临时磁盘，不把直接注册QA当正式硬资源任务。五原端口仍无监听，`logs/stock-course-execution-16-runtime.json`，未重启PG/Redis/前后端/connector/worker、未应用迁移、未恢复paused-user AI。**未执行**原PG、真实HTTP、本人浏览器、真实来源/PIT/经济标签/封存消费、五年/全市场/容量、期货在线功能和完整发布回退，不以离线ASGI/哈希一致替代。

## 人工路径和下一四包

获明确运行许可及独立部署门禁通过后，在A股研究/因子/回测页主动打开本人模型历史；默认应说明未开放。经验证019/存储/登记和读取Flag后检查分页→详情→固定参数/窗口/重要性→原始报告；切用户/盘/市场清旧内容，GET不能训练或评价。现在不让用户在停机页面进行假验收，也不提供不存在的训练按钮。

下一轮：①正式研究/训练任务的016兼容演进设计和实现，旧数据队列不误抢；②版本数据/因子/ML/组合/基准串入有界持久任务与结果分区登记，完成取消/恢复/幂等；③股票训练/可视化报告及历史参数恢复，复用此真实模型历史而不重造；④明确运行/预算后执行019等原PG迁移与并发、真实数据和全部市场规则/五年/本人/发布回退验收。仍保留12:05/17:10续做，不标课程或整个系统完成。
