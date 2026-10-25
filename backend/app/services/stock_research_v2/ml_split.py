"""Bounded walk-forward folds on the caller's explicit trading-session axis."""
from dataclasses import dataclass
import hashlib
import json

import pandas as pd

from app.schemas.stock_ml import TimeSplit
from app.schemas.stock_ml_experiment import WalkForwardSpec
from app.services.stock_research_v2.ml_dataset import MLSamples, MLFold, frame_fingerprint, split_samples

METHOD_VERSION = "stock-session-walk-forward-v1"


@dataclass(frozen=True)
class FoldManifest:
    fold_id: str
    fold: MLFold
    manifest: dict


def _distribution(raw, retained, cutoff=None):
    kept = set(zip(retained.decision_date, retained.instrument_id))
    reasons = {"missing_all_features": 0, "missing_label": 0, "label_overlap": 0, "invalid_query": 0}
    for row in raw.itertuples():
        if (row.decision_date, row.instrument_id) in kept:
            continue
        if not row.feature_available:
            reason = "missing_all_features"
        elif pd.isna(row.target_return) or pd.isna(row.label_end):
            reason = "missing_label"
        elif cutoff is not None and row.label_end >= pd.Timestamp(cutoff):
            reason = "label_overlap"
        else:
            reason = "invalid_query"
        reasons[reason] += 1
    query = retained.groupby("decision_date").size()
    return {"candidate_rows": len(raw), "retained_rows": len(retained), "candidate_dates": int(raw.decision_date.nunique()),
        "effective_dates": int(retained.decision_date.nunique()), "min_query_size": int(query.min()) if len(query) else 0,
        "max_query_size": int(query.max()) if len(query) else 0, "missing_all_features": int((~raw.feature_available).sum()),
        "missing_labels": int(raw.target_return.isna().sum()), "exclusion_reasons": reasons,
        "label_levels": {str(int(level)): int(count) for level, count in retained.relevance.dropna().value_counts().sort_index().items()},
        "date_counts": {str(day.date()): int(count) for day, count in query.items()}}


def make_time_folds(samples, spec):
    if not isinstance(samples, MLSamples):
        raise ValueError("需要已构建的固定版本ML样本")
    spec = WalkForwardSpec.model_validate(spec)
    rows = samples.candidates
    if rows.duplicated(["decision_date", "instrument_id"]).any() or list(sorted(rows.decision_date.dt.date.unique())) != spec.sessions:
        raise ValueError("样本日期与显式交易日轴不一致或证券日期重复")
    columns = ["decision_date", "instrument_id", "label_start", "label_end", "target_return", "relevance", *samples.feature_names]
    result = []
    for number in range(spec.fold_count):
        train_end = spec.warmup_sessions + spec.train_sessions + number * spec.test_sessions
        train_start = spec.warmup_sessions if spec.mode == "expanding" else train_end - spec.train_sessions
        validation_end = train_end + spec.validation_sessions
        test_end = validation_end + spec.test_sessions
        dates = spec.sessions
        split = TimeSplit(train_start=dates[train_start], train_end=dates[train_end-1],
            validation_start=dates[train_end], validation_end=dates[validation_end-1], test_start=dates[validation_end], test_end=dates[test_end-1])
        fold = split_samples(samples, split)
        raw_train = rows.loc[rows.decision_date.between(pd.Timestamp(split.train_start), pd.Timestamp(split.train_end))]
        raw_validation = rows.loc[rows.decision_date.between(pd.Timestamp(split.validation_start), pd.Timestamp(split.validation_end))]
        manifest = {"method_version": METHOD_VERSION, "fold_number": number, "mode": spec.mode,
            "split": split.model_dump(mode="json"), "warmup_dates": [str(day) for day in dates[:spec.warmup_sessions]],
            "session_dates": {"train": [str(day) for day in dates[train_start:train_end]],
                "validation": [str(day) for day in dates[train_end:validation_end]], "test": [str(day) for day in dates[validation_end:test_end]]},
            "purged_samples": fold.purge_counts, "test_role": "terminal_holdout" if number == spec.fold_count-1 else "walk_forward_oos",
            "sample_sha256": {name: frame_fingerprint(frame, columns) for name, frame in
                [("train", fold.train), ("validation", fold.validation), ("test_candidates", fold.test_candidates)]},
            "distributions": {"train": _distribution(raw_train, fold.train, split.validation_start),
                "validation": _distribution(raw_validation, fold.validation, split.test_start),
                "test_candidates": _distribution(fold.test_candidates, fold.test_candidates)},
            "holdout_registry_verified": False}
        identifier = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        result.append(FoldManifest(identifier, fold, {**manifest, "fold_id": identifier}))
    return result
