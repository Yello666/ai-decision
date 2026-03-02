from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_current_merchant
from app.core.responses import success
from app.db.mysql import get_db
from app.schemas.content import ContentGenerateRequest
from app.services.content_service import list_contents, create_content

router = APIRouter(prefix="/content", tags=["content"])

# 根据热点建议生成内容（图文/视频）
@router.post("/generate")
def generate_content(
    payload: ContentGenerateRequest,
    current_merchant=Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    generated_text = (
        f"[MVP placeholder] Generated content for '{payload.title}': {payload.prompt}"
    )
    content = create_content(
        db,
        current_merchant.shopify_store_id,
        payload.title,
        payload.prompt,
        generated_text,
    )
    return success(content)


# 查看生成的内容（首次生成后存储于数据库，可再次查看）
@router.get("/list")
def content_list(
    skip: int = 0,
    limit: int = 20,
    current_merchant=Depends(get_current_merchant),
    db: Session = Depends(get_db),
):
    items = list_contents(db, current_merchant.shopify_store_id, skip, limit)
    return success(items)
