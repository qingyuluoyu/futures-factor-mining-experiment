"""因子实验室 API 路由聚合"""

from fastapi import APIRouter

from app.api.factor_lab.backtest_api import router as backtest_router
from app.api.factor_lab.favorites_api import router as favorites_router
from app.api.factor_lab.history_api import router as history_router
from app.api.factor_lab.llm_api import router as llm_router
from app.api.factor_lab.search_api import router as search_router

router = APIRouter(prefix="/api/factor-lab", tags=["因子实验室"])
router.include_router(search_router)
router.include_router(backtest_router)
router.include_router(history_router)
router.include_router(favorites_router)
router.include_router(llm_router)
