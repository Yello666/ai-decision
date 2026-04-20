import math

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.api.deps import get_current_merchant
from app.core.hot_trends_cache import get_hot_trends_cached
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.hotspot import (
    BrandObject,
    HotspotBatchMatchRequest,
    HotspotMatchResponse,
    HotspotTrendRequest,
    PaginatedTrendResponse,
)
from app.services.hostpot_service.analyse_matching_degree import (
    batch_match_hotspot_for_brand_async,
)
from app.services.hostpot_service.collect_hostspot import collect_and_format_hot_data_async
from app.services.merchant_service import get_brand_by_merchant_id

router = APIRouter(prefix="/hotspot", tags=["hotspot"])


@router.post("/hot-trends", response_model=PaginatedTrendResponse, summary="获取热点数据（分页）")
async def get_hot_trends(request: HotspotTrendRequest):
    """
    获取热点趋势数据（分页）。
    后端固定缓存 50 条全量数据，前端按 page / page_size 翻页，每页最多 10 条，最多 5 页。
    """
    try:
        all_trends = await get_hot_trends_cached(
            request.platforms,
            loader=collect_and_format_hot_data_async,
        )

        total = len(all_trends)
        total_pages = math.ceil(total / request.page_size) if total else 0
        start = (request.page - 1) * request.page_size
        end = start + request.page_size
        items = all_trends[start:end]

        return PaginatedTrendResponse(
            items=items,
            total=total,
            page=request.page,
            page_size=request.page_size,
            total_pages=total_pages,
        )
    except Exception as e:
        msg = str(e).replace("\n", " ").replace("\\n", " ").strip()
        raise HTTPException(status_code=500, detail=f"获取热点数据失败：{msg}")



# 2.热点批量匹配（需登录；品牌信息由服务端从当前商户读取）
@router.post("/match", response_model=List[HotspotMatchResponse])
async def match_hotspot(
    request: HotspotBatchMatchRequest,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    """
    批量热点匹配：
    - 需登录；品牌信息根据当前登录商户自动从数据库读取
    - 每个 (品牌, 热点) 组合只分析一次，命中缓存则复用
    - 未命中的热点并行批量调用 LLM，Prompt 中品牌信息只写一次
    """
    brand_model = get_brand_by_merchant_id(db, current_merchant.id)
    if not brand_model:
        raise HTTPException(status_code=400, detail="brand_not_set")

    brand = BrandObject(
        name=brand_model.name,
        core_value=brand_model.core_value,
        mainly_sold_products=brand_model.mainly_sold_products,
        tone=brand_model.tone,
        audience=brand_model.audience.split(",") if brand_model.audience else [],
    )

    try:
        return await batch_match_hotspot_for_brand_async(
            trends=request.trends,
            brand=brand,
            llm_model=request.llm_model,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量匹配失败：{str(e)}")

