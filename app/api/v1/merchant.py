from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.schemas.hotspot import BrandObject
from app.schemas.merchant import BrandToneUpdate, MerchantOut
from app.models import Merchant, Brand

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
    # 检查是否已存在品牌信息
    brand = db.query(Brand).filter(Brand.merchant_id == current_merchant.id).first()
    
    audience_str = ",".join(brand_object.audience) if brand_object.audience else ""
    
    if brand:
        # 如果存在，则更新
        brand.name = brand_object.name
        brand.core_value = brand_object.core_value
        brand.industry = brand_object.industry
        brand.tone = brand_object.tone
        brand.audience = audience_str
    else:
        # 如果不存在，则创建
        brand = Brand(
            shopify_store_id=current_merchant.shopify_store_id,
            merchant_id=current_merchant.id,
            name=brand_object.name,
            core_value=brand_object.core_value,
            industry=brand_object.industry,
            tone=brand_object.tone,
            audience=audience_str
        )
        db.add(brand)
    
    db.commit()
    db.refresh(brand)
    
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
        brand_object: BrandObject,
        current_merchant: Merchant = Depends(get_current_merchant),
        db: Session = Depends(get_db)
):
    brand = db.query(Brand).filter(Brand.merchant_id == current_merchant.id).first()
    if not brand:
        # 如果没有品牌信息，可以选择抛出异常或者直接调用 set_brand_info 的逻辑
        # 这里选择创建一个新的
        brand = Brand(
            shopify_store_id=current_merchant.shopify_store_id,
            merchant_id=current_merchant.id,
            name=brand_object.name,
            core_value=brand_object.core_value,
            industry=brand_object.industry,
            tone=brand_object.tone,
            audience=",".join(brand_object.audience) if brand_object.audience else ""
        )
        db.add(brand)
    else:
        brand.name = brand_object.name
        brand.core_value = brand_object.core_value
        brand.industry = brand_object.industry
        brand.tone = brand_object.tone
        brand.audience = ",".join(brand_object.audience) if brand_object.audience else ""
    
    db.commit()
    db.refresh(brand)
    
    res = BrandObject(
        name=brand.name,
        core_value=brand.core_value,
        industry=brand.industry,
        tone=brand.tone,
        audience=brand.audience.split(",") if brand.audience else []
    )
    return success(res)