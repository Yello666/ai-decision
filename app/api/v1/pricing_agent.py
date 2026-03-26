import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.models import Merchant
from app.services.pricing_agent import run_pricing_analysis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pricing-agent", tags=["pricing-agent"])


class PricingAgentRequest(BaseModel):
    """动态调价分析请求体。"""

    product_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="用户选择的 Shopify product ID 列表（最多 5 个）",
    )


@router.post("/analyze")
async def analyze_pricing(
    payload: PricingAgentRequest,
    merchant: Merchant = Depends(get_current_merchant),
):
    """根据商品 ID 列表执行调价分析，返回结构化 JSON 建议。"""
    logger.info(
        "Pricing Analysis Request - Merchant: %s (ID: %s), Products: %s",
        merchant.name,
        merchant.id,
        payload.product_ids,
    )

    try:
        result = await run_pricing_analysis(merchant, payload.product_ids)
        logger.info(
            "Pricing Analysis Success - Merchant: %s, Products analyzed: %d",
            merchant.name,
            len(payload.product_ids),
        )
        return success(data=result)
    except Exception:
        logger.exception(
            "Pricing Analysis Error - Merchant: %s, Products: %s",
            merchant.name,
            payload.product_ids,
        )
        raise HTTPException(status_code=500, detail="调价分析服务暂时不可用，请稍后重试")
