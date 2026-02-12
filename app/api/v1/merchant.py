from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.schemas.merchant import BrandToneUpdate, MerchantOut
from app.models import Merchant

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

#接收shopifyAPP前端传入的BrandObject，并存储到数据库当中。
@router.post("/brand-info")
def set_brand_info():
    pass

#更新Brand信息
@router.put("/brand-info")
def update_brand_info():
    pass