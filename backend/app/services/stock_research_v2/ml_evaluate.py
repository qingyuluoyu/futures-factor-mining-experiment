"""Descriptive ranking evaluation, separate from training and execution returns."""
import math

import numpy as np
import pandas as pd

METHOD_VERSION = "stock-ranking-evaluation-v1"
KEYS = ["decision_date", "instrument_id"]


def _validate(frame, value):
    if not isinstance(frame, pd.DataFrame) or not set(KEYS + [value]) <= set(frame) or frame.duplicated(KEYS).any():
        raise ValueError("评估输入缺字段或证券日期重复")
    if not pd.api.types.is_datetime64_dtype(frame.decision_date) or frame.decision_date.isna().any() or not frame.decision_date.equals(frame.decision_date.dt.normalize()):
        raise ValueError("评估日期须为明确日频日期")
    from app.schemas.stock_research_v2 import equity_identifier
    for instrument in frame.instrument_id.unique():
        equity_identifier(instrument)
    if (not frame[value].isna().all() and not pd.api.types.is_numeric_dtype(frame[value])) or pd.api.types.is_bool_dtype(frame[value]) or np.isinf(frame[value].to_numpy(dtype=float)).any():
        raise ValueError("评估分数或标签非有限/非数值")


def _ndcg(rows, at):
    relevance = np.minimum(4, np.floor((rows.target_return.rank(method="average") - 1) * 5 / len(rows)))
    if relevance.nunique() < 2:
        return None
    gains = pd.Series(np.power(2., relevance)-1, index=rows.index)
    ordered = rows.assign(gain=gains).sort_values("score", ascending=False, kind="stable")
    discounts = 1 / np.log2(np.arange(len(rows)) + 2)
    dcg, position = 0., 0
    # Average gain over all score ties, including ties crossing the cut-off.
    for _, tied in ordered.groupby("score", sort=False):
        width = len(tied)
        dcg += float(tied.gain.mean()) * float(discounts[position:min(position+width, at)].sum())
        position += width
    ideal = float((np.sort(gains.to_numpy())[::-1][:at] * discounts[:at]).sum())
    return dcg / ideal if ideal > 0 else None


def evaluate_prediction(predictions, candidates, *, groups=5, ndcg_at=5):
    if type(groups) is not int or not 2 <= groups <= 10 or type(ndcg_at) is not int or not 1 <= ndcg_at <= 300:
        raise ValueError("分组或NDCG截断无效")
    _validate(predictions, "score"); _validate(candidates, "target_return")
    if candidates.empty or len(candidates) > 2_000_000:
        raise ValueError("评估候选为空或超过预算")
    if (candidates.target_return.dropna() < -1).any():
        raise ValueError("收益标签不得低于-100%")
    unknown = predictions.merge(candidates[KEYS], on=KEYS, how="left", indicator=True)
    if unknown._merge.eq("left_only").any():
        raise ValueError("预测包含固定候选集以外的证券日期")
    joined = candidates[KEYS+["target_return"]].merge(predictions[KEYS+["score"]], on=KEYS, how="left", validate="one_to_one")
    joined["score"] = pd.to_numeric(joined.score)
    joined["target_return"] = pd.to_numeric(joined.target_return)
    score, label = joined.score.notna(), joined.target_return.notna()
    counts = {"candidates": len(joined), "evaluated": int((score & label).sum()),
        "missing_prediction_only": int((~score & label).sum()), "missing_label_only": int((score & ~label).sum()),
        "missing_prediction_and_label": int((~score & ~label).sum())}
    daily = []
    for day, rows in joined.groupby("decision_date", sort=True):
        paired = rows.dropna(subset=["score", "target_return"])
        rank_ic = float(paired.score.rank().corr(paired.target_return.rank())) if len(paired) >= 3 and paired.score.nunique() > 1 and paired.target_return.nunique() > 1 else None
        ndcg = _ndcg(paired, min(ndcg_at, len(paired))) if len(paired) >= 2 else None
        scored = rows.dropna(subset=["score"]).copy()
        group_rows, reason = [], None
        if len(scored) and scored.score.nunique() >= groups:
            scored["bucket"] = np.minimum(groups-1, np.floor((scored.score.rank(method="average")-1) * groups / len(scored))).astype(int)
            if scored.bucket.nunique() != groups:
                reason = "insufficient_unique_scores"
            else:
                for bucket, members in scored.groupby("bucket", sort=True):
                    group_rows.append({"group": int(bucket)+1, "members": len(members), "missing_labels": int(members.target_return.isna().sum()),
                        "mean_label_return": float(members.target_return.mean()) if members.target_return.notna().all() else None})
        else:
            reason = "insufficient_unique_scores"
        daily.append({"date": str(day.date()), "candidate_count": len(rows), "evaluated_count": len(paired),
            "rank_ic": rank_ic if rank_ic is None or math.isfinite(rank_ic) else None, "ndcg": ndcg, "groups": group_rows, "groups_reason": reason})
    def mean(key):
        values = [row[key] for row in daily if row[key] is not None]
        return float(np.mean(values)) if values else None
    return {"method_version": METHOD_VERSION, "status": "exploratory_not_certified", "holdout_registry_verified": False,
        "counts": counts, "daily": daily, "mean_rank_ic": mean("rank_ic"), "mean_ndcg": mean("ndcg"),
        "rank_ic_dates": sum(row["rank_ic"] is not None for row in daily), "ndcg_dates": sum(row["ndcg"] is not None for row in daily),
        "groups": groups, "ndcg_at": ndcg_at, "portfolio_backtest": {"available": False, "reason": "execution_and_benchmark_required"},
        "limitations": ["标签缺失与无预测逐日保留；均值只描述可评价日期，不代表完整区间收益。", "分组为标签收益描述，未计成交、费用或重叠窗口，不能称可成交净值。", "最终测试封存与消费审计未登记，不认证独立样本外。"]}
