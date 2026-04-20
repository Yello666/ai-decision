import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from langgraph.types import Command

from app.api.deps import get_current_merchant
from app.core.responses import success
from app.models import Merchant
from app.services.pricing_service import run_pricing_analysis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pricing-analyze", tags=["pricing-analyze"])


class ProductSelection(BaseModel):
    """单个商品的调价目标。

    - 仅传 ``product_id``：对该商品下所有 variant 统一调价（原有行为）。
    - 同时传 ``variant_id``：只对该 variant 调价。
    """

    product_id: int = Field(..., description="Shopify product ID")
    variant_id: Optional[int] = Field(
        default=None,
        description="可选的 Shopify variant ID；指定后只对该 variant 调价",
    )


class PricingAgentRequest(BaseModel):
    """动态调价分析请求体。

    每个条目支持 per-variant 选择：
    - 只传 ``product_id`` → 对该商品所有 variant 统一调价；
    - 同时传 ``variant_id`` → 只对该 variant 调价。
    """

    products: Optional[List[ProductSelection]] = Field(
        default=None,
        min_length=1,
        max_length=5,
        description="用户选择的商品（及可选的 variant）列表，最多 5 个",
    )


    @model_validator(mode="after")
    def _normalize(self) -> "PricingAgentRequest":
        if not self.products:
            raise ValueError("必须提供 products")
        return self

    @property
    def selections(self) -> List[dict]:
        """Return normalized selections as plain dicts for service layer."""
        return [
            {"product_id": s.product_id, "variant_id": s.variant_id}
            for s in (self.products or [])
        ]


class PricingReviewCommand(BaseModel):
    """Human review 回调指令。"""
    action: Literal["approve", "reject", "regenerate"]
    feedback: str | None = Field(
        default=None, description="Regenerate 时提供的修正意见，例如“降价幅度太小”"
    )
    thread_id: str = Field(..., description="会话 thread_id，用于 resume")


@router.post("/analyze")
async def analyze_pricing(
    payload: PricingAgentRequest,
    merchant: Merchant = Depends(get_current_merchant),
):
    """首次启动定价分析，运行至 human_review 触发 interrupt。"""
    selections = payload.selections
    logger.info(
        "Pricing Analysis Request - Merchant: %s (ID: %s), Selections: %s",
        merchant.name,
        merchant.id,
        selections,
    )

    try:
        result = await run_pricing_analysis(merchant, selections)
        logger.info(
            "Pricing Analysis Success - Merchant: %s, Selections analyzed: %d",
            merchant.name,
            len(selections),
        )
        return success(data=result)
    except Exception:
        logger.exception(
            "Pricing Analysis Error - Merchant: %s, Selections: %s",
            merchant.name,
            selections,
        )
        raise HTTPException(status_code=500, detail="调价分析服务暂时不可用，请稍后重试")


@router.post("/review")
async def review_pricing_decision(
    command: PricingReviewCommand,
    merchant: Merchant = Depends(get_current_merchant),
):
    """Callback 接口：处理用户对 pricing 建议的 Approve / Reject / Regenerate 决策。"""
    logger.info("Pricing Review Command: %s for thread %s", command.action, command.thread_id)

    try:
        # 根据 action 构造 LangGraph Command
        if command.action == "approve":
            langgraph_cmd = Command(resume="approve")
        elif command.action == "reject":
            langgraph_cmd = Command(resume="reject")
        elif command.action=="regenerate":
            langgraph_cmd = Command(
                resume={
                    "command": "regenerate",
                    "feedback": command.feedback or "请重新生成更合理的定价建议",
                }
            )
        else:
            langgraph_cmd = Command(resume="reject")

        result = await run_pricing_analysis(
            merchant=merchant,
            selections=[],  # resume 时不需要，state 中已持久化
            thread_id=command.thread_id,
            command=langgraph_cmd,
            feedback=command.feedback,
        )

        logger.info("Pricing review completed with action: %s", command.action)
        return success(data={"status": command.action, "result": result})
    except Exception:
        logger.exception("Pricing Review Error - thread: %s", command.thread_id)
        raise HTTPException(status_code=500, detail="审核处理失败，请稍后重试")
