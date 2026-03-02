from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.schemas.hotspot import BrandObject, BrandUpdate
from app.schemas.merchant import BrandToneUpdate, MerchantOut
from app.models import Merchant, Brand
from app.services.merchant_service import create_or_update_brand

router = APIRouter(prefix="/merchant", tags=["merchant"])


# 登录之后获取信息
@router.get("/info")
def get_info(current_merchant: Merchant = Depends(get_current_merchant)):
    return success(MerchantOut.model_validate(current_merchant))

#登录之后设置品牌调性
@router.post("/brand-tone")
def set_brand_tone(
    payload: BrandToneUpdate,
    current_merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db)
):
    current_merchant.brand_tone = payload.brand_tone
    db.commit()
    db.refresh(current_merchant)
    return success(MerchantOut.model_validate(current_merchant))

#登录之后设置品牌信息
#接收shopifyAPP前端传入的BrandObject，并存储到数据库当中。
@router.post("/brand-info")
def set_brand_info(
        brand_object: BrandObject,
        current_merchant: Merchant = Depends(get_current_merchant),
        db: Session = Depends(get_db)
):
    brand = create_or_update_brand(db, current_merchant, brand_object.model_dump())
    
    # 将 audience 转回 list 返回
    res = BrandObject(
        name=brand.name,
        core_value=brand.core_value,
        industry=brand.industry,
        tone=brand.tone,
        audience=brand.audience.split(",") if brand.audience else []
    )
    return success(res)


#登录之后更新Brand信息
@router.put("/brand-info")
def update_brand_info(
        brand_object: BrandUpdate,
        current_merchant: Merchant = Depends(get_current_merchant),
        db: Session = Depends(get_db)
):
    # 使用 exclude_unset=True 实现部分更新
    update_data = brand_object.model_dump(exclude_unset=True)
    brand = create_or_update_brand(db, current_merchant, update_data)
    
    res = BrandObject(
        name=brand.name,
        core_value=brand.core_value,
        industry=brand.industry,
        tone=brand.tone,
        audience=brand.audience.split(",") if brand.audience else []
    )
    return success(res)