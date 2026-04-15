import logging
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from langgraph.types import Command

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
            product_ids=[],  # resume 时不需要
            thread_id=command.thread_id,
            command=langgraph_cmd,
            feedback=command.feedback,
        )

        logger.info("Pricing review completed with action: %s", command.action)
        return success(data={"status": command.action, "result": result})
    except Exception:
        logger.exception("Pricing Review Error - thread: %s", command.thread_id)
        raise HTTPException(status_code=500, detail="审核处理失败，请稍后重试")
