"""商户自上传热点 CRUD + 推荐：与 YouTube 全局热点完全独立，按 merchant_id 隔离。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.models import Merchant
from app.schemas.hotspot import BrandObject
from app.schemas.own_hotspot import (
    OwnHotspotCreate,
    OwnHotspotRecommendRequest,
    OwnHotspotRecommendResponse,
    OwnHotspotUpdate,
)
from app.services.hotspot_service.own_hotspot import (
    build_own_recommended_hotspots,
    create_for_merchant,
    delete_for_merchant,
    get_for_merchant,
    list_for_merchant,
    update_for_merchant,
)
from app.services.merchant_service import get_brand_by_merchant_id

router = APIRouter(prefix="/own-hotspot", tags=["own-hotspot"])


# --- 查：列表（按 created_at 倒序，最新在前）---


@router.get("")
def list_own_hotspots(
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
    limit: int = Query(50, ge=1, le=250, description="每页条数"),
):
    data = list_for_merchant(db, merchant.id, limit=limit)
    return success(data=data)


# --- 查：单条 ---


@router.get("/{hotspot_id}")
def get_own_hotspot(
    hotspot_id: int,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
):
    row = get_for_merchant(db, merchant.id, hotspot_id)
    if row is None:
        raise HTTPException(status_code=404, detail="hotspot_not_found")
    return success(data=row)


# --- 增 ---


@router.post("")
def create_own_hotspot(
    body: OwnHotspotCreate,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
):
    row = create_for_merchant(db, merchant.id, body)
    return success(data=row)


# --- 改（部分字段可选，未传的不更新）---


@router.put("/{hotspot_id}")
def update_own_hotspot(
    hotspot_id: int,
    body: OwnHotspotUpdate,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
):
    row = update_for_merchant(db, merchant.id, hotspot_id, body)
    if row is None:
        raise HTTPException(status_code=404, detail="hotspot_not_found")
    return success(data=row)


# --- 删 ---


@router.delete("/{hotspot_id}")
def delete_own_hotspot(
    hotspot_id: int,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
):
    ok = delete_for_merchant(db, merchant.id, hotspot_id)
    if not ok:
        raise HTTPException(status_code=404, detail="hotspot_not_found")
    return success(data={"id": hotspot_id, "deleted": True})


# --- 推荐：对该商户全部热点跑品牌匹配并按契合度过滤 ---


@router.post(
    "/recommend",
    response_model=OwnHotspotRecommendResponse,
    summary="推荐用户自有热点（按契合度过滤）",
)
async def recommend_own_hotspots(
    request: OwnHotspotRecommendRequest,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
):
    """
    对当前商户在 hotspots 表中的全部热点做品牌匹配，剔除契合度低于 min_compatibility_score 的条目后返回。

    匹配模型与 /hotspot/match 一致（配置项 LLM_MODEL_36_PLUS），并复用 (brand, trend) 匹配缓存。
    与 YouTube 推荐互不影响。
    """
    brand_model = get_brand_by_merchant_id(db, merchant.id)
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
        items, analyzed_count = await build_own_recommended_hotspots(
            db=db,
            merchant_id=merchant.id,
            brand=brand,
            min_compatibility_score=request.min_compatibility_score,
        )
    except Exception as e:
        msg = str(e).replace("\n", " ").replace("\\n", " ").strip()
        raise HTTPException(status_code=500, detail=f"推荐用户热点失败：{msg}")

    return OwnHotspotRecommendResponse(
        items=items,
        min_compatibility_score=request.min_compatibility_score,
        analyzed_count=analyzed_count,
    )
