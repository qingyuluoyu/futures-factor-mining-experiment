# 执行12恢复：真实Parquet、来源读取与018显式迁移

**已实现待验证**。UI第四轮之后继续原D4，不另起总计划。本轮三个包：真实锁版引擎和分区写读验证；私有登记/回滚及带来源元数据的列式读取；018默认只读检查的显式迁移runner。尚未接原任务执行器/API/页面，不能称完整数据集worker或课程已可用。

## 实际文件和数据流

修改`backend/app/services/stock_research_v2/columnar.py`、`backend/tests/test_stock_columnar_store.py`、`backend/tests/test_stock_columnar_registry.py`；新增`backend/scripts/migrate_stock_columnar.py`、`backend/tests/test_stock_columnar_migration.py`。原columnar_store.py、Schema、ORM、018 SQL、锁版要求、015/016/017和所有期货/共享配置未修改。文档仅追加恢复事实与最新指针。

1. 原执行12下载阻塞已解除：使用同一官方PyPI wheel，curl单次上限45秒、0重试，实际约4秒下载28,567,581字节；SHA256与原锁定`cc903e1069e9dd5e9dcf780324c0112e27e051e422ecfaff574fb33ed65d9160`一致。验证wheel元数据为PyArrow26.0.0、CPython3.12 Windows x64、Apache-2.0、Python≥3.11、无Requires-Dist后，用uv离线安装到`logs/stock-columnar-qa`，未向期货venv安装。默认解释器确认无法import pyarrow；只有本轮显式PYTHONPATH的测试进程加载该target。target文件约88.7MB，不宣称这是包含缓存的全部磁盘占用。
2. 固定私有DatasetRef→核验父归档与来源→原ParquetStore写不可变内容→登记→拥有者/JWT盘读取→验证每个分区→分批返回。每批新增来源metadata，来自重新校验的不可变SourcePage，含provider/market/currency/units/时区/日历证据/复权/采集时间等，排除原始observations；输出deepcopy防止调用方改单位污染后续批次。缺少请求字段所属数据类别明确422 `columnar_fields_unavailable`，不返回空成功或补值。仍是`source_observations_not_aligned`、`observation_only`，没有财报日频对齐或研究/回测资格升级。
3. 018 runner沿现有股票迁移方式：默认check=True、检查事务READ ONLY；先验证015完整反射结构和原SQL校验和，再在显式apply时锁20261024018、执行仅本018两表一索引DDL、完整反射列/类型/默认值/主键/唯一性/FK/检查/索引与validated状态、事务登记校验和。lock_timeout5秒、statement_timeout30秒；CLI限制原loopbackPG5433，不自动迁移先决条件、不启动服务。新的固定DDL切分器剔除纯注释，拒绝其它模块或破坏性语句；不是通用SQL解析器。

SQL018内容未改变，旧v1与持久结果不变，无金融公式、币种/成交量单位转换或历史可得时间修改。小数成交量`0.01`、真零、显式null、字段缺失各自保留；Decimal文本不经float截断。真实Parquet头PAR1、确定性SHA、财报原始披露UTC与报告期、1024行row group和128行投影批次已在QA实际文件验证。单位为原规范化SourcePage的单位，不冒充源接口原始单位。

## 实际命令与验证结果

根目录命令：

```text
curl.exe --fail --location --retry 0 --connect-timeout 5 --max-time 45 --output logs/stock-columnar-wheels/pyarrow-26.0.0-cp312-cp312-win_amd64.whl https://files.pythonhosted.org/packages/54/3c/1783aab1dac28e175dcf26dfc7123725efc474caecaed91e8a34cb89cad0/pyarrow-26.0.0-cp312-cp312-win_amd64.whl
uv pip install --python backend/.venv/Scripts/python.exe --target logs/stock-columnar-qa --no-index --find-links logs/stock-columnar-wheels --no-deps --require-hashes -r backend/requirements-stock-columnar.txt
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/migrate_stock_columnar.py --help
```

backend目录，进程局部`PYTHONPATH=(Resolve-Path ../logs/stock-columnar-qa).Path`：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_columnar_store.py tests/test_stock_columnar_registry.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_columnar_registry.py tests/test_stock_columnar_migration.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_columnar_store.py tests/test_stock_columnar_registry.py tests/test_stock_columnar_migration.py -q
.venv/Scripts/python.exe -X utf8 -m pytest <logs/stock-course-execution-12-resume-test-files.log的实际文件清单> -q
```

首次真实引擎测试3失败/4通过，因新增QA样例观察日期晚于测试工厂默认采集时间；只修测试样例为1月7日采集，**没有放宽生产时间校验**（`...-first.log`）。随后metadata读取先红1/6通过（`...-reader-red.log`）；缺类别查询和runner补测先红（`...-contract-red.log`）。该轮SQL静态用例误把FK的ON DELETE识别成DELETE语句，修为语句起始匹配，不放行真正写旧表的语句。实现后12目标通过（`...-target.log`），再补大row group、幂等冲突/回滚实际Parquet复用等回归。

一次自审为018末尾纯注释加入DDL切分/拒绝无关语句，先红1/3通过（`...-migration-red.log`）；初实现正则遗漏合法CREATE INDEX的ON目标表，受影响测试1失败/86通过（`...-regression-final.log`），修为显式允许本索引ON本表。最终**87受影响后端通过、0失败、0跳过，128条既有utcnow弃用警告，5.25秒**（`...-regression-accepted.log`）。包含实际Parquet、SQLite测试登记/回滚/用户盘隔离、旧归档/PIT/任务claim/监督/迁移静态回归；具体命令参数文件清单已保存。`--help`在移除PYTHONPATH的子进程及默认venv均通过，不依赖PG或Arrow import。

前缀`...`均为`logs/stock-course-execution-12-resume`。没有重跑前端/TSC/生产构建，本轮未改前端。**未执行真实PG迁移/反射/并发、原HTTP、本人浏览器、生产任务进程、五年性能、金融样本认证和发布回退验收**；SQLite只用于离线服务测试，不冒充PG验证。018仅创建runner，未check或apply数据库；015/016/017亦未应用。

依赖证据：`...-wheel-audit.json`、`...-runtime-isolation.json`、`...-download.log`及`...-install.log`。uv跨盘硬链接失败后正常复制到隔离target，无环境升级。[Apache官方安全公告](https://security.apache.org/projects/arrow/)核对26.0.0不受CVE-2026-92287影响，并核对较旧IPC/反序列化问题的受影响范围；当前读取不配置KMS、不启用Arrow扩展。没有做全面漏洞扫描，原OSV连接失败仍保留，不能据此宣称依赖绝无漏洞。

## 保护、兼容与剩余门禁

修改前UI第四轮1144清单verify通过，其他执行者仅root。最终**1145**清单为`logs/stock-course-execution-12-resume-accepted-inventory.json`；仅既有columnar.py声明变化，其他**1143旧保护SHA不变**，新增runner被原guard追踪。两个既有后端测试与新增迁移测试另列SHA，不把它们漏算成“只改一个文件”。`...-accepted-supplemental-sha.json`保存6项脚本/测试/018SQL/依赖锁SHA，需和主清单一起核验。首次证据脚本误以为runner不被原guard收录，断言失败后按实际新增清单校正；没有重设未知漂移。较早`...-final-inventory.json`为DDL自审前历史，**不要作为最新清单**。

字节备份`...-backups.json`，最终差异`...-accepted-changes.diff`，保护断言`...-accepted-preservation.json`，guard日志`...-accepted-guard.log`。源码回退只撤销columnar.py本轮读取增量及新runner引用，保持新入口关闭；无生产数据库/数据写入需要回滚。未来应用018后，代码回退应保留新增表/不可变产物，停止新建并维持历史可解释，不能用DROP伪称安全回退。

原3001/8002/8110/5433/6380未监听（`...-runtime.json`），未拉起任何服务或恢复paid AI。新增开销为公开依赖下载和本地计算；金融数据/模型新增调用0，无盲重导、补行情或付费调用。

仍缺：Parquet实际接入原有有界Job/父级续租/取消/发布链；真实PG与018应用/并发；数据来源资格和历史PIT/股票池/复权认证；孤儿内容GC；百万行/五年/512MiB进程边界下的列式性能。回滚测试证明未登记receipt不可读、重试复用原内容，不代表孤儿文件GC已经完成。新API/页面/执行器均保持未公开，不把原JSON观察任务自动替换成列式任务。

下一四包：原有固定claim的列式子程序/父级结果与取消恢复接线；PG迁移和并发真实验收（需运行许可，不能自动启动）；自定义FactorRef精确版本/PIT资格→IC及报告；可成交回测基准/复现后接必做本地LightGBM。本人路径在真实链路接通后为本人股票盘确认固定数据→列式任务→取消/恢复→同版本读取→IC/回测/报告，当前不能提供假入口让用户验收。
