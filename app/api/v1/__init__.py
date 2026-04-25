from fastapi import APIRouter
from .auth import router as auth_router
from .merchant import router as merchant_router
from .hotspot import router as hotspot_router
from .pricing_analyze import router as pricing_agent_router
from .products import router as products_router
from .generations import router as generate_router
from .video_thread import router as video_thread_router
from .video_tasks import router as video_task_router
from .seedance2 import router as seedance2_router
from .upload import router as upload_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(merchant_router)
router.include_router(hotspot_router)
router.include_router(pricing_agent_router)
router.include_router(products_router)
router.include_router(generate_router)
router.include_router(video_thread_router)
router.include_router(video_task_router)
router.include_router(seedance2_router)
router.include_router(upload_router)
