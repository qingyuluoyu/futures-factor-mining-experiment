"""模拟交易常量"""

from decimal import Decimal

# 每月可领取虚拟练习金（人民币）
MONTHLY_CLAIM_AMOUNT: Decimal = Decimal("1000000.00")

# 领取时区
CLAIM_TZ_NAME: str = "Asia/Shanghai"

# 以下仅为极端兜底；正常走 product_specs + 用户设置
DEFAULT_MARGIN_RATE: Decimal = Decimal("0.12")
DEFAULT_MULTIPLIER: int = 10
