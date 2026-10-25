"""Stock calculation kernel. Input provenance/qualification must be checked by its caller."""
from dataclasses import dataclass
import hashlib
import json
import re

import numpy as np
import pandas as pd

from app.schemas.stock_custom_factor import CustomFactorView
from app.schemas.stock_research_v2 import equity_identifier
from app.services.stock_factors.catalog import FACTOR_BY_ID, factor_policy
from app.services.stock_factors.engine import MarketData, PreprocessConfig, build_factor_matrix, preprocess, METHOD_VERSION as FORMULA_VERSION
from app.services.stock_research_v2.expressions import CompiledFactor, compile_expression, evaluate_expression, METHOD_VERSION as EXPRESSION_VERSION
from app.services.stock_research_v2.custom_factors import ALLOWED_FIELDS

METHOD_VERSION = "stock-factor-values-2026-10-25.v1"
DEPENDENCIES = {"pe_ttm": "pe_ttm", "pb": "pb", "revenue_yoy": "revenue_yoy", "netprofit_yoy": "netprofit_yoy", "roe": "roe", "gross_margin": "gross_margin", "ret_20d": "close", "vol_20d": "close", "turnover_20d": "turnover"}


@dataclass(frozen=True)
class FactorDefinition:
    key: str
    version: str
    direction: str
    expression: CompiledFactor | None = None


def builtin_factor(identifier):
    if identifier not in FACTOR_BY_ID:
        raise ValueError("未知内置因子")
    spec = FACTOR_BY_ID[identifier]
    payload = {"formula_method": FORMULA_VERSION, "id": identifier, "formula": spec.formula, "direction": spec.direction, "policy": factor_policy(identifier)}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return FactorDefinition(identifier, digest, spec.direction)


def custom_factor(record):
    saved = CustomFactorView.model_validate(record)
    compiled = compile_expression(saved.expression, allowed_fields=ALLOWED_FIELDS)
    if saved.disabled or saved.formula_version != EXPRESSION_VERSION or saved.expression_version != compiled.version or saved.fields != list(compiled.fields) or saved.lookback != compiled.lookback:
        raise ValueError("自定义因子停用或固定版本/依赖不一致")
    return FactorDefinition(f"custom:{saved.id}:{saved.revision}:{saved.expression_version}", compiled.version, saved.direction, compiled)


async def resolve_factors(service, builtin_ids, custom_refs):
    from app.schemas.stock_dataset_inspection import ResearchRequirements
    from app.services.stock_research_v2.custom_factors import CustomFactorService
    if not isinstance(service, CustomFactorService):
        raise ValueError("需要绑定当前用户和盘的因子服务")
    request = ResearchRequirements(factor_ids=builtin_ids, custom_factors=custom_refs)
    result = [builtin_factor(identifier) for identifier in request.factor_ids]
    for reference in request.custom_factors:
        compiled = await service.resolve(**reference.model_dump())
        record = await service.get(reference.id, reference.revision)
        definition = custom_factor(record)
        if definition.version != compiled.version:
            raise ValueError("固定因子版本在解析期间变化")
        result.append(definition)
    return result


def validate_frame(frame, reference=None):
    if not isinstance(frame, pd.DataFrame) or frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("需要非空日期证券面板")
    if frame.index.tz is not None or frame.index.hasnans or not frame.index.equals(frame.index.normalize()) or not frame.index.is_unique or not frame.index.is_monotonic_increasing or not frame.columns.is_unique:
        raise ValueError("交易日和证券轴必须唯一、有序、明确为日频日期")
    for column in frame.columns:
        equity_identifier(column)
    if reference is not None and (not frame.index.equals(reference.index) or not frame.columns.equals(reference.columns)):
        raise ValueError("字段日期和证券轴不一致，未静默重排或补齐")
    if any(isinstance(value, (bool, np.bool_)) for value in frame.to_numpy().flat):
        raise ValueError("布尔值不是金融数值")
    try:
        return frame.astype(float).replace([np.inf, -np.inf], np.nan)
    except (TypeError, ValueError):
        raise ValueError("字段不是数值面板") from None


@dataclass(frozen=True)
class FactorBundle:
    values: dict
    definitions: tuple
    coverage: dict
    method_version: str = METHOD_VERSION
    validation_status: str = "calculation_only_unverified_inputs"


def compute_factors(fields, factors, *, config=None, industry=None, eligible=None, max_cells=2_000_000):
    if type(max_cells) is not int or not 0 < max_cells <= 2_000_000 or not isinstance(factors, list) or not 1 <= len(factors) <= 25 or any(not isinstance(item, FactorDefinition) for item in factors) or len({item.key for item in factors}) != len(factors):
        raise ValueError("因子选择或单批面板预算无效")
    required = {"close"}
    for definition in factors:
        if not isinstance(definition, FactorDefinition) or definition.direction not in {"high", "low"}:
            raise ValueError("需要固定版本的因子定义")
        if definition.expression is None:
            if definition != builtin_factor(definition.key):
                raise ValueError("内置因子方法版本变化")
            required.add(DEPENDENCIES[definition.key])
        else:
            if not re.fullmatch(r"custom:[a-f0-9-]{36}:[1-9][0-9]*:[a-f0-9]{64}", definition.key) or not definition.key.endswith(definition.version) or definition.version != definition.expression.version:
                raise ValueError("自定义因子引用无效")
            required.update(definition.expression.fields)
    config = config or PreprocessConfig()
    if not isinstance(config, PreprocessConfig):
        raise ValueError("预处理配置类型无效")
    if config.mktcap_neutral:
        required.add("marketcap")
    if not isinstance(fields, dict) or not required <= fields.keys():
        raise ValueError("所选因子的字段依赖缺失")
    close = fields["close"]
    if not isinstance(close, pd.DataFrame) or close.size * (len(required) + len(factors)) > max_cells:
        raise ValueError("单批因子计算超过面板预算，须列式分块执行")
    panels = {name: validate_frame(fields[name], close) for name in sorted(required)}
    if ((panels["close"] <= 0) & panels["close"].notna()).any().any():
        raise ValueError("价格必须为正或明确缺失")
    if eligible is None:
        eligible = pd.DataFrame(True, index=close.index, columns=close.columns)
    if not isinstance(eligible, pd.DataFrame) or not eligible.index.equals(close.index) or not eligible.columns.equals(close.columns) or not all(pd.api.types.is_bool_dtype(dtype) for dtype in eligible.dtypes) or eligible.isna().any().any():
        raise ValueError("历史股票池资格面板无效")
    if config.industry_neutral and (not isinstance(industry, pd.DataFrame) or not industry.index.equals(close.index) or not industry.columns.equals(close.columns)):
        raise ValueError("行业中性化必须提供逐日历史行业")
    panels = {name: value.where(eligible) for name, value in panels.items()}
    source = MarketData(panels["close"], {**{name: value for name, value in panels.items() if name in {"pb", "pe_ttm"}}, **({"turnover_rate": panels["turnover"]} if "turnover" in panels else {})},
        {name: value for name, value in panels.items() if name in {"roe", "gross_margin", "revenue_yoy", "netprofit_yoy"}}, {}, {})
    values, coverage = {}, {}
    for definition in factors:
        if definition.expression is None:
            raw = build_factor_matrix(source, definition.key).copy()
        else:
            clean = {key: value.where(value > 0) if key in {"pe_ttm", "pb"} else value for key, value in panels.items()}
            raw = evaluate_expression(definition.expression, clean, max_cells=max_cells)
        finite = np.isfinite(raw)
        domain = finite & (raw > 0) if definition.key in {"pb", "pe_ttm"} else finite
        strict_missing = definition.expression is not None or definition.key in {"pb", "pe_ttm", "ret_20d", "vol_20d", "turnover_20d"}
        mask = eligible & domain if strict_missing else eligible
        marketcap = panels.get("marketcap")
        if config.industry_neutral:
            processed = raw.copy() * np.nan
            for date in raw.index:
                sectors = {code: value for code, value in industry.loc[date].items() if isinstance(value, str) and value}
                processed.loc[[date]] = preprocess(raw.loc[[date]], config, sectors, marketcap.loc[[date]] if marketcap is not None else None, eligible=mask.loc[[date]])
        else:
            processed = preprocess(raw, config, None, marketcap, eligible=mask)
        values[definition.key] = processed.where(mask)
        coverage[definition.key] = {"raw_finite": int(finite.to_numpy().sum()), "excluded_domain": int((finite & ~domain).to_numpy().sum()),
            "processed_finite": int(np.isfinite(values[definition.key]).to_numpy().sum()), "missing_policy": "exclude_without_imputation" if strict_missing else "cross_sectional_median_if_enabled"}
    return FactorBundle(values, tuple(factors), coverage)
