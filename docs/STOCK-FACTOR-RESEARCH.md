# A 股因子研究模块说明（题目 2：多因子选股与回测平台）

本模块在期货平台基础上扩展 A 股市场，实现「因子构建 → 因子检验 → 组合回测」
的完整量化研究链路。数据来自本地 Tushare 导入库，前端入口为侧边栏
「因子研究」（`/factor-research`）。

## 1. 数据接入

```bash
# 沪深300 全量导入（股票池/前复权日线/每日估值/季度财务）
uv run python scripts/import-stock-data.py --universe hs300 --start-date 20180101

# 中证800 / 指定股票
uv run python scripts/import-stock-data.py --universe csi800
uv run python scripts/import-stock-data.py --codes 600519,000001
```

数据源为 Tushare 协议代理（`.env` 的 `TUSHARE_TOKEN`），落地四类表：

| 表 | 内容 | 关键字段 |
|---|---|---|
| `stock_instruments` | 标的静态信息 | code/name/exchange/industry/list_date |
| `kline_history`（market_type='stock'） | 前复权日线 | OHLC + volume |
| `stock_valuation` | 每日估值 | pe_ttm/pb/total_mv/circ_mv/turnover_rate |
| `stock_financial` | 季度财务 | roe/gross_margin/revenue_yoy/netprofit_yoy/eps/bps + **publish_date** |

复权口径：`前复权价 = 原始价 × adj_factor(t) / adj_factor(区间最后交易日)`。
市值由 Tushare 的万元换算为亿元。

**双市场隔离**：`kline_history` 与 `watchlist` 均有 `market_type` 列
（futures/stock），期货与 A 股数据、自选彻底分离；用户的市场偏好存于
`users.market_type`，顶栏「期货 / A 股」切换。

## 2. 因子库（六类 9 个）

| 因子 | 类别 | 公式 | 方向 | 数据源 |
|---|---|---|---|---|
| pe_ttm | 价值 | 市盈率TTM | ↓小好 | 每日估值 |
| pb | 价值 | 市净率 | ↓小好 | 每日估值 |
| revenue_yoy | 成长 | 营业总收入同比增长率 | ↑大好 | 季度财报 |
| netprofit_yoy | 成长 | 归母净利润同比增长率 | ↑大好 | 季度财报 |
| roe | 质量 | 净资产收益率 | ↑大好 | 季度财报 |
| gross_margin | 质量 | 销售毛利率 | ↑大好 | 季度财报 |
| ret_20d | 动量 | close[t]/close[t-20]-1 | ↑大好 | 量价 |
| vol_20d | 波动 | 近20日收益率标准差×√244 | ↓小好 | 量价 |
| turnover_20d | 流动性 | 近20日日均换手率 | ↓小好 | 每日估值 |

自定义因子：表达式因子内核（`factor_lab`）与量价数据通用，A 股日线
可直接复用；本模块的 9 个因子即按同一元数据规范注册。

## 3. 因子预处理

逐交易日横截面执行，顺序固定：

1. **去极值**：MAD 法（median ± 5×1.4826×MAD）或 3σ 法，可关闭；
2. **缺失值填充**：截面中位数；
3. **行业中性化**：减去同行业截面均值（行业哑变量回归的等价简化）；
4. **市值中性化**：对 log(总市值) 做逐日 OLS 取残差；
5. **标准化**：截面 Z-Score。

行业分类取自 `stock_basic`（Tushare 行业字段），市值取 `daily_basic.total_mv`。

## 4. 因子检验（`POST /api/stock/factors/analysis`）

- **IC**：每个截面日计算因子（方向校正后）与未来 N 日收益的 Pearson 相关；
  输出 IC 均值、IC 标准差、IC IR（年化）、秩 IC 均值/IR、IC>0 占比；
- **IC 衰减**：因子值与未来 1/5/10/20/40/60 日收益的秩 IC 曲线；
- **分组收益**：每月末按因子值分 5 组，统计各组未来 N 日平均收益，
  检验单调性；
- **多空净值**：每日做多头部组、做空尾部组，等权持有，累计净值曲线。

## 5. 组合回测（`POST /api/stock/backtest`）

参数：因子（多选等权合成）、调仓频率（周/月）、持股数量、加权方式
（等权/市值加权/因子加权）、手续费率、滑点率。

流程：每个调仓日按合成因子值选前 N 只 → 按权重建仓 → 持有至下一调仓日
→ 期末按区间收益计算组合收益并扣除换手成本。绩效指标：总收益、年化收益、
年化波动、夏普、索提诺、卡玛、最大回撤、胜率、**换手率**（题目 2 要求项）。

## 6. 偏差防护（题目 2 难点一）

| 偏差 | 防护措施 |
|---|---|
| **前视偏差** | 财务因子按 `publish_date`（公告日）对齐：决策日只使用已披露财报；量价因子只使用 T 日及之前数据；未来收益统一用 `shift(-N)` 计算 |
| **幸存者偏差** | 股票池为指数当前成分股，退市股票不在池内——这是本实现的已知边界，报告中须显式声明；后续可导入退市历史成分改进 |
| **停牌不可交易** | 停牌日无 K 线的股票不在当日截面中，天然不会被选中；建仓/持有均跳过无行情日 |
| **成本真实性** | 手续费 + 滑点按每期换手率（权重变动绝对值之和）计费，可配置 |

## 7. 复现性

相同参数 + 相同数据版本 → 两次回测结果完全一致：因子计算为确定性
横截面运算，无随机成分；参数快照随回测结果返回（`params` 字段）。

## 8. 目录结构

```
backend/app/
  data/tushare_client.py      # Tushare HTTP 客户端（限速+重试）
  data/stock_history.py       # 股票池/日线/估值/财务获取（批量）
  models/stock.py             # stock_instruments / stock_valuation / stock_financial
  services/stock_factors/
    catalog.py                # 因子目录（六类 9 个）
    engine.py                 # 面板/预处理/IC检验/组合回测引擎
  api/stock.py                # 股票池/K线/估值/财务 API
  api/stock_factors.py        # 因子目录/检验/回测 API
frontend/src/
  lib/stock-api.ts            # A股 API 封装
  components/market/stock-*   # 股票池侧栏/K线图/行情视图
  components/factor-research/ # 因子研究页图表
  app/(main)/factor-research/ # 因子研究页（检验+回测）
scripts/import-stock-data.py  # 数据导入（增量续跑）
scripts/import-stock-loop.sh  # 崩溃自恢复的导入循环
```
