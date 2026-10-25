from dataclasses import dataclass

from app.services.stock_factors.catalog import FACTOR_BY_ID
from app.services.stock_factors.engine import ResearchDataError


VALUATION_FIELDS = ("pe_ttm", "pb", "turnover_rate", "total_mv")
FINANCIAL_FIELDS = ("roe", "gross_margin", "revenue_yoy", "netprofit_yoy")
DEPENDENCY_VERSION = "stock-research-dependencies-v1"


@dataclass(frozen=True)
class DependencyPlan:
    valuation_fields: tuple[str, ...]
    financial_fields: tuple[str, ...]
    selective: bool

    def panel_cells(self, price_cells, valuation_cells, has_financial_rows):
        return price_cells + len(self.valuation_fields) * valuation_cells + (len(self.financial_fields) * price_cells if has_financial_rows else 0)

    def manifest(self):
        return {"version": DEPENDENCY_VERSION, "selection": "factor_dependencies" if self.selective else "legacy_all_fields",
            "price_fields": ["close"], "valuation_fields": list(self.valuation_fields), "financial_fields": list(self.financial_fields)}


def resolve_dependencies(factor_ids=None, *, mktcap_neutral=False, weight="equal"):
    if type(mktcap_neutral) is not bool or weight not in ("equal", "mktcap", "factor"):
        raise ResearchDataError("invalid_research_dependencies", "中性化或权重依赖参数无效", 422)
    if factor_ids is None:
        return DependencyPlan(VALUATION_FIELDS, FINANCIAL_FIELDS, False)
    if not isinstance(factor_ids, (list, tuple)) or not 1 <= len(factor_ids) <= len(FACTOR_BY_ID) or any(not isinstance(item, str) or item not in FACTOR_BY_ID for item in factor_ids) or len(set(factor_ids)) != len(factor_ids):
        raise ResearchDataError("invalid_research_dependencies", "因子依赖必须是非空、已知且不重复的有界列表", 422)
    valuation = {"turnover_rate" if item == "turnover_20d" else item for item in factor_ids if FACTOR_BY_ID[item].source == "valuation"}
    financial = {item for item in factor_ids if FACTOR_BY_ID[item].source == "financial"}
    if mktcap_neutral or weight == "mktcap":
        valuation.add("total_mv")
    return DependencyPlan(tuple(item for item in VALUATION_FIELDS if item in valuation), tuple(item for item in FINANCIAL_FIELDS if item in financial), True)
