# 自部署数据配置指南

适用对象：从 GitHub 下载源码自行部署的本系统用户。

## 总原则：所有数据自行获取，本仓库不提供任何服务和数据

本系统**不内置、不附带、不托管任何行情与历史数据服务**。部署后的全部数据
（实时报价、K 线、历史行情）均由用户自行从公开渠道获取：

| 公开渠道 | 用途 | 是否需要凭据 |
| --- | --- | --- |
| 新浪财经公开接口 | 实时报价 / 近期 K 线（默认渠道） | 不需要 |
| AKShare 公开数据 | 历史日线（因子挖掘主料） | 不需要 |
| 天勤 TqSdk（快期账户） | 历史分钟线（可选） | 免费注册账户 |
| openctp / 期货公司 CTP | 实盘行情（可选） | 用户自有账户 |

各行情接口的频控、IP 限流与数据质量风险由用户自行承担。遇到限流时降低拉取
频率或更换渠道（渠道开关见 `.env.example`）。

## 0. 构建镜像

docker-compose 引用本地构建的镜像（不拉取任何远端私有仓库）：

```bash
# 在仓库根目录执行
docker build -t qihuo-backend:local backend/
docker build -t qihuo-frontend:local frontend/

# 可选：CTP 行情桥（openctp 虚拟盘 / 期货公司实盘前置）
docker build -t qihuo-ctp-bridge:local -f backend/ctp-bridge.Dockerfile backend/
```

启动时通过环境变量指定镜像：

```bash
QIHUO_BACKEND_IMAGE=qihuo-backend:local \
QIHUO_FRONTEND_IMAGE=qihuo-frontend:local \
docker compose up -d
```

## 1. 必填配置

复制 `.env.example` 为 `.env`，必填项只有基础架构与安全密钥：

```ini
POSTGRES_USER=qihuo
POSTGRES_PASSWORD=<自己设定>          # compose 自带 postgres_db 容器
JWT_SECRET_KEY=<随机串>
AES_ENCRYPTION_KEY=<base64 的 32 字节> # .env.example 内附生成命令
```

Redis 由 docker-compose 自带，无需额外配置。`APP_ENV` 不再控制自动建表；
开发和生产启动均只读检查已有表/列，缺失时拒绝启动，不自动修改数据库。

以下数据相关变量**全部可以留空**，留空即降级、不报错：

```ini
TICKDATA_POSTGRES_HOST=    # 深历史 K 线库。仅当用户自备同类 PG 时填写；
                           # 留空则分钟线深历史自动降级（见第 3 节）
TQSDK_USER=                # 仅跑分钟线导入脚本时需要
TQSDK_PASSWORD=
KLINE_REMOTE_BASE=         # K 线远端兜底源，可选
```

## 2. 数据库创建与数据表

docker-compose 只自带 Redis；**PostgreSQL 需要用户自行创建实例**（推荐 16+，
官方在 postgres:18 上验证）。可以用宿主机安装、云数据库或自建容器，例如：

```bash
docker run -d --name qihuo-postgres -e POSTGRES_USER=qihuo \
  -e POSTGRES_PASSWORD=自己设定 -e POSTGRES_DB=qihuo \
  -p 5432:5432 postgres:18
```

然后在库内建用户与数据库（如实例初始化时未创建）：

```sql
CREATE USER qihuo WITH PASSWORD '自己设定';
CREATE DATABASE qihuo OWNER qihuo;
```

`.env` 中把连接指向自己的实例（容器部署时 `POSTGRES_HOST` 填容器名或宿主 IP）。

### 启动前完成受审查的数据库迁移

2026-10-19 起取消旧启动自动DDL。已有兼容数据库仅执行只读表/列存在检查，
不检查或自动修复全部类型、索引、约束和数据版本。缺表/缺列会报告
`SchemaReadinessError` 并停止，不会改动数据。

现有股票回放/研究/盘后/归档迁移在 `backend/migrations/` 和相应版本化脚本中，
启用能力还必须通过各自迁移checksum检查。完整期货旧库与空库基线仍在P01工作包中，
尚未提供可认证的全新空库部署路径。不要手工拼表或运行旧 `_init_tables` 作为替代；
先备份、评审兼容迁移及前向恢复方案，再在隔离库验证。下面数据表说明不构成建库迁移。

### 自己灌数据的表结构

**`kline_history`（日线历史，`import-kline-history.py` 的写入目标）**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 自增主键 |
| code | varchar(10) | 品种代码（大写，如 AU、RB），索引 |
| trade_date | date | 交易日期，索引 |
| open / high / low / close | double precision | 开高低收 |
| volume | integer | 成交量 |
| hold | integer | 持仓量，默认 0 |

唯一约束 `(code, trade_date)`，重复导入按此键覆盖更新。

**`kline_minute`（分钟历史，`import-kline-minute.py` 的写入目标）**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | integer | 自增主键 |
| code | varchar(10) | 品种代码（大写），索引 |
| symbol | varchar(30) | 具体合约符号，如 au2412 |
| period | varchar(4) | 周期，默认只入库 1m（5/15/30/60m 由系统从 1m 现算） |
| ts | timestamptz | K 线时间戳，索引 |
| open / high / low / close | double precision | 开高低收 |
| volume | integer | 成交量 |
| hold | integer | 持仓量，默认 0 |

唯一约束 `(code, period, ts)`。

### 可选：自备深历史库（tickdata 接口）

配置 `TICKDATA_POSTGRES_*` 时，系统期望目标库中存在按以下结构建好的 `kline`
表（这张表**不会**被自动创建，需自建并自行灌数）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| period | smallint | 周期分钟数：1 / 5 / 15 / 30 / 60 / 1440 |
| exchange | varchar | 交易所代码（查询未直接使用，预留） |
| instrument | varchar | 具体合约，如 rb2610 |
| ts | timestamptz | K 线时间戳 |
| open / high / low / close | double precision | 开高低收 |
| volume | bigint | 成交量 |
| amount | double precision | 成交额（预留） |
| position | integer | 持仓量 |

主键 `(period, instrument, ts)`；数据量大时建议按 `ts` 月分区（官方库 264 个月
分区，单次查询毫秒级）。未配置该库时系统自动跳过，分钟线深历史降级为
"新浪近期 + 本地导入数据"。

## 3. 因子挖掘数据初始化（必做一次）

超级因子挖掘依赖本地历史库，部署后需自行导入公开数据，否则日线挖掘会报
"品种在历史库中无日线数据"。

### 日线（因子挖掘主料，零凭据）

```bash
uv run python scripts/import-kline-history.py              # 全量导入（所有品种）
uv run python scripts/import-kline-history.py --code rb    # 单品种
```

数据源为 AKShare 的主力连续合约日线（无需任何账号），写入本地 PostgreSQL
的 `kline_history` 表。数据量约几十个品种 × 各自全量日线，首次导入需数分钟。

### 分钟线深历史（可选，需快期免费账户）

```bash
uv run python scripts/import-kline-minute.py               # 全量 1m
uv run python scripts/import-kline-minute.py --code rb --period 15m
```

数据源为天勤 TqSdk（凭据填 `.env` 的 `TQSDK_USER` / `TQSDK_PASSWORD`）。
免费版每品种每次最多约 8964 根（1m 约 2-3 周，15m 约 6 个月）。

## 4. 数据留空时的功能表现

| 功能 | 数据来源 | 不导入 / 留空时 |
| --- | --- | --- |
| 实时报价、近期 K 线 | 新浪直连（默认渠道 `sina`） | 正常（这是必装渠道，无凭据） |
| 因子挖掘 - 日线 | 上节 AKShare 导入 | 报错并提示先跑导入脚本（不可用） |
| 因子挖掘 - 分钟线 | 新浪近期 +（可选）天勤导入 +（可选）tickdata 深历史 | 可用但深度浅：仅新浪近期数据（约 1-2 个月），各周期根数受上限截断 |
| 量化回测 / 实盘任务 | 上述 K 线链路 | 同上，长周期回测深度受限 |

`TICKDATA_POSTGRES_*` 是为自备"全周期历史 K 线 PostgreSQL 库"的用户预留的可选
接口；未配置时系统自动跳过该源、降级到新浪近期 + 本地导入数据，不影响其他功能。
