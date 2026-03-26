from typing import List

from pydantic import BaseModel, Field


class PricingAnalysisItemLLM(BaseModel):
    """Schema for LLM output — does NOT include competitor_info_url."""

    product_name: str = Field(description="Product name")
    current_price: float = Field(description="Current listed price")
    stock_status: str = Field(description="Human-readable stock status")
    competitor_price_summary: str = Field(
        description="Concise summary of competitor price range and average"
    )
    recommended_price: float = Field(description="AI-recommended price")
    suggested_action: str = Field(
        description="One of: Increase price / Decrease price moderately / "
        "Decrease price significantly / Keep current price"
    )
    detailed_reason: str = Field(
        description="Reasoning referencing competitor data and inventory"
    )
    confidence_score: float = Field(
        ge=0, le=100, description="Confidence score 0-100"
    )


class PricingAnalysisResponseLLM(BaseModel):
    """Wrapper used by JsonOutputParser to validate LLM output."""

    pricing_analysis: List[PricingAnalysisItemLLM]


class PricingAnalysisItem(BaseModel):
    """Full pricing item returned to the frontend (includes competitor URLs)."""

    product_name: str = Field(description="Product name")
    current_price: float = Field(description="Current listed price")
    stock_status: str = Field(description="Human-readable stock status")
    competitor_price_summary: str = Field(
        description="Concise summary of competitor price range and average"
    )
    competitor_info_url: List[str] = Field(
        default_factory=list,
        description="Reference URLs of competitor listings used in the analysis",
    )
    recommended_price: float = Field(description="AI-recommended price")
    suggested_action: str = Field(
        description="One of: Increase price / Decrease price moderately / "
        "Decrease price significantly / Keep current price"
    )
    detailed_reason: str = Field(
        description="Reasoning referencing competitor data and inventory"
    )
    confidence_score: float = Field(
        ge=0, le=100, description="Confidence score 0-100"
    )


class PricingAnalysisResponse(BaseModel):
    """Top-level wrapper returned to the frontend."""

    pricing_analysis: List[PricingAnalysisItem]
