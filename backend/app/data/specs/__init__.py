"""期货品种交易规格数据"""

from app.data.specs.product_specs import (
    extract_product_code,
    get_product_spec,
    list_all_product_specs,
)

__all__ = [
    "extract_product_code",
    "get_product_spec",
    "list_all_product_specs",
]
