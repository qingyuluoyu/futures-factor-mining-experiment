"""合约树 schema —— 按品种代码分组的合约树响应模型"""

from pydantic import BaseModel, Field


class ContractItemSchema(BaseModel):
    """单个合约项"""

    symbol: str = Field(..., description="合约代码,如 rb2610")
    month: int = Field(..., description="交割月份 1-12")
    year: int = Field(..., description="交割年份,如 2026")
    volume: int = Field(0, description="当日成交量")
    is_master: bool = Field(False, description="是否为主力合约")
    is_secondary: bool = Field(False, description="是否为次主力合约")


class CodeTreeSchema(BaseModel):
    """单个品种代码下的合约树"""

    code: str = Field(..., description="品种代码,如 RB")
    name: str = Field(..., description="品种中文名,如 螺纹钢")
    exchange: str = Field(..., description="交易所,如 上期所")
    master: str | None = Field(None, description="主力合约 symbol")
    secondary: str | None = Field(None, description="次主力合约 symbol")
    contracts: list[ContractItemSchema] = Field(default_factory=list, description="该品种下全部交割月份合约")
