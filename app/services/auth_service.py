import json
import uuid
from typing import Optional, Dict, Any
import httpx
from sqlalchemy.orm import Session
from app.core.security import verify_password, get_password_hash
from app.models import Merchant
from app.core.config import get_settings
from app.schemas.merchant import MerchantCreate
from app.db.redis import get_redis_client

settings = get_settings()

def authenticate_merchant(db: Session, username:str, password: str) -> Optional[Merchant]:
    merchant = (
        db.query(Merchant)
        .filter(Merchant.name == username)
        .first()
    )
    if not merchant:
        return None
    if not verify_password(password, merchant.password_hash):
        return None
    return merchant

def get_shopify_auth_url(shop_domain: str, state: str) -> str:
    scopes = "read_products,read_content,read_themes" 
    redirect_uri = settings.SHOPIFY_REDIRECT_URI
    return f"https://{shop_domain}/admin/oauth/authorize?client_id={settings.SHOPIFY_API_KEY}&scope={scopes}&redirect_uri={redirect_uri}&state={state}"

async def initiate_registration(merchant_in: MerchantCreate) -> str:
    state = str(uuid.uuid4())
    redis = get_redis_client()
    # Store registration data in Redis, expire in 15 minutes
    await redis.setex(
        f"registration:{state}",
        900,
        merchant_in.model_dump_json()
    )
    await redis.close()
    
    return get_shopify_auth_url(merchant_in.shopify_domain, state)

async def exchange_code_for_token(shop_domain: str, code: str) -> str:
    url = f"https://{shop_domain}/admin/oauth/access_token"
    data = {
        "client_id": settings.SHOPIFY_API_KEY,
        "client_secret": settings.SHOPIFY_API_SECRET,
        "code": code
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=data)
        response.raise_for_status()
        return response.json()["access_token"]

async def get_shop_info(shop_domain: str, access_token: str) -> Dict[str, Any]:
    url = f"https://{shop_domain}/admin/api/{settings.SHOPIFY_API_VERSION}/shop.json"
    headers = {
        "X-Shopify-Access-Token": access_token
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()["shop"]

async def complete_registration(db: Session, state: str, code: str, shop_domain: str) -> Merchant:
    redis = get_redis_client()
    data = await redis.get(f"registration:{state}")
    await redis.close()
    
    if not data:
        raise ValueError("Invalid or expired state")
    
    merchant_in = MerchantCreate.model_validate_json(data)
    print(f"注册时填写的shopify_domain: '{merchant_in.shopify_domain}'")
    print(f"Shopify回调传回的shop参数: '{shop_domain}'")
    
    # Verify shop domain matches
    if merchant_in.shopify_domain != shop_domain:
         raise ValueError("Shop domain mismatch")

    # Exchange token
    access_token = await exchange_code_for_token(shop_domain, code)
    
    # Get Shop Info
    shop_info = await get_shop_info(shop_domain, access_token)
    
    # Extract info
    shopify_store_id = str(shop_info.get("id"))
    # shop.category is not always available in shop.json, but user said:
    # "Through Shopify API /admin/api/2024-01/shop.json Get... Key field: shop.category"
    # I will try to get it, or default to None.
    # Actually, recent Shopify API might put this elsewhere or require specific scopes/fields.
    # But I will follow user instruction.
    shopify_category = shop_info.get("category") # Note: Verify if this field exists in response. 
    # Usually it's in a different taxonomy or inferred. But I'll stick to user req.
    
    # Create Merchant
    print(f"输入的密码:{merchant_in.password}")
    db_merchant = Merchant(
        email=merchant_in.email,
        password_hash=get_password_hash(merchant_in.password),
        name=merchant_in.name,
        shopify_store_id=shopify_store_id,
        shopify_domain=merchant_in.shopify_domain,
        shopify_category=shopify_category,
        brand_tone="professional", # Default
        is_active=True
    )
    
    # Check if exists (email or store_id)
    # email不可以重复
    existing = db.query(Merchant).filter(
        (Merchant.email == merchant_in.email) | (Merchant.shopify_store_id == shopify_store_id)
    ).first()
    
    if existing:
        raise ValueError("Merchant already exists with this email or shop")

    db.add(db_merchant)
    db.commit()
    db.refresh(db_merchant)
    
    # Cleanup
    redis = get_redis_client()
    await redis.delete(f"registration:{state}")
    await redis.close()
    
    return db_merchant
