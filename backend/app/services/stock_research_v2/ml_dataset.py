"""Label-aware time splitting. Candidate membership never depends on future labels."""
from dataclasses import dataclass
import hashlib
import json

import numpy as np
import pandas as pd

from app.schemas.stock_ml import TimeSplit
from app.services.stock_research_v2.factor_values import validate_frame

METHOD_VERSION = "stock-ml-date-purge-v1"
RESERVED = {"decision_date", "instrument_id", "label_start", "label_end", "target_return", "relevance", "feature_available"}


@dataclass(frozen=True)
class MLSamples:
    candidates: pd.DataFrame
    feature_names: tuple
    min_group_size: int


@dataclass(frozen=True)
class MLFold:
    train: pd.DataFrame
    validation: pd.DataFrame
    test_candidates: pd.DataFrame
    feature_names: tuple
    spec: TimeSplit
    purge_counts: dict
    min_group_size: int = 5


def build_ml_dataset(features, returns, windows, *, min_group_size=5):
    if not isinstance(features, dict) or not 1 <= len(features) <= 16 or type(min_group_size) is not int or not 2 <= min_group_size <= 1000:
        raise ValueError("ML特征数量或组大小无效")
    if any(not isinstance(name, str) or not 1 <= len(name) <= 200 or name in RESERVED for name in features):
        raise ValueError("特征不能包含标签、身份或控制字段")
    target = validate_frame(returns)
    if target.size * (len(features) + 1) > 2_000_000 or (target < -1).any().any():
        raise ValueError("标签收益低于-100%或样本超过单批预算")
    matrices = {name: validate_frame(frame, target) for name, frame in features.items()}
    if not isinstance(windows, dict) or set(windows) != set(target.index):
        raise ValueError("每个信号日须有明确标签窗口或明确缺失")
    batches = []
    for day in target.index:
        window = windows[day]
        if window is not None:
            if not isinstance(window, tuple) or len(window) != 2:
                raise ValueError("标签窗口格式无效")
            start, end = map(pd.Timestamp, window)
            if not day < start < end or start not in target.index or end not in target.index:
                raise ValueError("标签起止须为信号日之后的明确交易日轴成员")
        else:
            start = end = pd.NaT
            if target.loc[day].notna().any():
                raise ValueError("有收益标签却没有可追溯结束日期")
        batch = pd.DataFrame({"decision_date": day, "instrument_id": target.columns, "label_start": start, "label_end": end,
            "target_return": target.loc[day].to_numpy(), **{name: frame.loc[day].to_numpy() for name, frame in matrices.items()}})
        batch["feature_available"] = batch[list(features)].notna().any(axis=1)
        batch["relevance"] = np.nan
        usable = batch.feature_available & batch.target_return.notna()
        values = batch.loc[usable, "target_return"]
        if len(values) >= min_group_size and values.nunique() >= 2:
            batch.loc[usable, "relevance"] = np.minimum(4, np.floor((values.rank(method="average") - 1) * 5 / len(values))).astype(int)
        batches.append(batch)
    return MLSamples(pd.concat(batches, ignore_index=True).sort_values(["decision_date", "instrument_id"], kind="stable").reset_index(drop=True), tuple(features), min_group_size)


def split_samples(samples, spec):
    if not isinstance(samples, MLSamples):
        raise ValueError("需要已构建的ML样本")
    spec = TimeSplit.model_validate(spec)
    rows = samples.candidates
    def segment(start, end):
        return rows.loc[rows.decision_date.between(pd.Timestamp(start), pd.Timestamp(end))].copy()
    def training(start, end, cutoff):
        batch = segment(start, end)
        usable = batch.relevance.notna() & batch.feature_available
        purged = int((usable & (batch.label_end >= pd.Timestamp(cutoff))).sum())
        batch = batch.loc[usable & (batch.label_end < pd.Timestamp(cutoff))]
        valid = batch.groupby("decision_date").agg(n=("instrument_id", "size"), levels=("relevance", "nunique"))
        dates = valid.index[(valid.n >= samples.min_group_size) & (valid.levels >= 2)]
        return batch.loc[batch.decision_date.isin(dates)].copy(), purged
    train, train_purge = training(spec.train_start, spec.train_end, spec.validation_start)
    validation, validation_purge = training(spec.validation_start, spec.validation_end, spec.test_start)
    test = segment(spec.test_start, spec.test_end)
    if train.decision_date.nunique() < 2 or validation.decision_date.nunique() < 1 or test.empty:
        raise ValueError("purge后训练/验证的有效日期组不足，或没有测试候选；未缩短区间或补标签")
    return MLFold(train, validation, test, samples.feature_names, spec, {"train": train_purge, "validation": validation_purge}, samples.min_group_size)


def frame_fingerprint(frame, columns):
    digest = hashlib.sha256(json.dumps(list(columns), ensure_ascii=False).encode())
    for row in frame.loc[:, columns].itertuples(index=False, name=None):
        values = []
        for value in row:
            if pd.isna(value): values.append(None)
            elif isinstance(value, pd.Timestamp): values.append(value.isoformat())
            elif isinstance(value, (float, np.floating)): values.append(float(value).hex())
            elif isinstance(value, (int, np.integer)): values.append(int(value))
            else: values.append(str(value))
        digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()
