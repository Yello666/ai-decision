from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.schemas.hotspot import BrandObject, BrandUpdate
from app.schemas.merchant import MerchantOut
from app.models import Merchant, Brand
from app.services.merchant_service import create_or_update_brand, get_brand_by_merchant_id

router = APIRouter(prefix="/merchant", tags=["merchant"])


# 登录之后获取信息
@router.get("/info")
def get_info(current_merchant: Merchant = Depends(get_current_merchant)):
    return success(MerchantOut.model_validate(current_merchant))
# 获取品牌信息
@router.get("/brand-info")
def get_brand_info(
        current_merchant: Merchant = Depends(get_current_merchant),
        db: Session = Depends(get_db)
):
    brand = get_brand_by_merchant_id(db, current_merchant.id)
    if not brand:
        return success(None, message="brand_not_set")

    res = BrandObject(
        name=brand.name,
        core_value=brand.core_value,
        mainly_sold_products=brand.mainly_sold_products,
        tone=brand.tone,
        audience=brand.audience.split(",") if brand.audience else []
    )
    return success(res)




# 登录之后设置品牌信息
#接收shopifyAPP前端传入的BrandObject，并存储到数据库当中。
@router.post("/brand-info")
def set_brand_info(
        brand_object: BrandObject,
        current_merchant: Merchant = Depends(get_current_merchant),
        db: Session = Depends(get_db)
):
    brand = create_or_update_brand(db, current_merchant, brand_object.model_dump(by_alias=True))
    
    # 将 audience 转回 list 返回
    res = BrandObject(
        name=brand.name,
        core_value=brand.core_value,
        mainly_sold_products=brand.mainly_sold_products,
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
    update_data = brand_object.model_dump(exclude_unset=True, by_alias=True)
    brand = create_or_update_brand(db, current_merchant, update_data)
    
    res = BrandObject(
        name=brand.name,
        core_value=brand.core_value,
        mainly_sold_products=brand.mainly_sold_products,
        tone=brand.tone,
        audience=brand.audience.split(",") if brand.audience else []
    )
    return success(res)