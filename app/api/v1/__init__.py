from fastapi import APIRouter
from .auth import router as auth_router
from .merchant import router as merchant_router
from .hotspot import router as hotspot_router
from .content import router as content_router
from .pricing_agent import router as pricing_agent_router
from .products import router as products_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(merchant_router)
router.include_router(hotspot_router)
router.include_router(content_router)
router.include_router(pricing_agent_router)
router.include_router(products_router)
