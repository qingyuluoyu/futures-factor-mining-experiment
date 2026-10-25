from typing import Annotated

from pydantic import AfterValidator

from app.data.instrument_scope import ensure_cny_futures_symbol


CnyFuturesSymbol = Annotated[str, AfterValidator(ensure_cny_futures_symbol)]
