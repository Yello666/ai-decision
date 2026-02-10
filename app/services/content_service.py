from sqlalchemy.orm import Session
from app.models import Content


def list_contents(db: Session, shopify_store_id: str, skip: int = 0, limit: int = 20):
    return (
        db.query(Content)
        .filter(Content.shopify_store_id == shopify_store_id)
        .order_by(Content.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_content(
    db: Session,
    shopify_store_id: str,
    title: str,
    prompt: str,
    generated_text: str,
):
    content = Content(
        shopify_store_id=shopify_store_id,
        title=title,
        prompt=prompt,
        generated_text=generated_text,
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    return content
