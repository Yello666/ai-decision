from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.responses import success
from app.services.pricing_agent import run_pricing_agent


router = APIRouter(prefix="/pricing-agent", tags=["pricing-agent"])


class PricingAgentRequest(BaseModel):
    """动态调价 Agent 请求体。"""

    query: str = Field(..., description="用户输入，例如：帮我看看这个商品价格是否需要调整")


@router.post("/analyze", response_model=dict)
def analyze_pricing(payload: PricingAgentRequest):
    """执行 ReAct 调价分析。"""

    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    result = run_pricing_agent(payload.query)
    return success(
        {
            "product_name": result.product_name,
            "own_price": result.own_price,
            "inventory_status": result.inventory_status,
            "competitor_prices": result.competitor_prices,
            "recommended_price": result.recommended_price,
            "action": result.action,
            "reasoning": result.reasoning,
            "raw_output": result.raw_output,
        }
    )
