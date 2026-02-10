from sqlalchemy.orm import Session
import httpx
from app.models import Hotspot
# from app.schemas.hotspot import AssessmentRequest, AssessmentResponse
from app.core.config import get_settings

settings = get_settings()


def list_hotspots(db: Session, shopify_store_id: str, skip: int = 0, limit: int = 20):
    return (
        db.query(Hotspot)
        .filter(Hotspot.shopify_store_id == shopify_store_id)
        .order_by(Hotspot.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_hotspot(db: Session, shopify_store_id: str, hotspot_id: int):
    return (
        db.query(Hotspot)
        .filter(Hotspot.shopify_store_id == shopify_store_id)
        .filter(Hotspot.id == hotspot_id)
        .first()
    )


# async def assess_hotspot_match(request: AssessmentRequest) -> AssessmentResponse:
#     # Prepare payload for AI service
#     payload = request.model_dump()
#
#     # Mocking response if URL is dummy or empty
#     if not settings.AUTODL_SERVICE_URL or "autodl-service-url" in settings.AUTODL_SERVICE_URL:
#          return AssessmentResponse(
#              match_score=85.5,
#              match_reason="The rising trend of 'dishwater coffee' aligns well with your fun and friendly brand tone, offering a playful marketing opportunity.",
#              brand_fit="High",
#              conversion_prediction="Moderate to High",
#              content_suggestion="Create a humorous video comparing your high-quality coffee with the viral trend.",
#              best_timing="Weekdays morning",
#              products_to_promote=["Premium Dark Roast", "Barista Kit"]
#          )
#
#     async with httpx.AsyncClient() as client:
#         response = await client.post(settings.AUTODL_SERVICE_URL, json=payload, timeout=30.0)
#         response.raise_for_status()
#         return AssessmentResponse(**response.json())
