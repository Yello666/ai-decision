from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.core.responses import success
from app.core.security import create_access_token, create_refresh_token, decode_refresh_token
from app.db.mysql import get_db
from app.schemas.auth import LoginSchema, RefreshRequest
from app.schemas.merchant import MerchantCreate
from app.services.auth_service import authenticate_merchant, initiate_registration, complete_registration

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/ping")
async def ping():
    return success({"message":"pong"})

@router.post("/register")
async def register(payload: MerchantCreate):
    auth_url = await initiate_registration(payload)
    return success({"auth_url": auth_url})


@router.get("/shopify/callback")
async def shopify_callback(
    code: str = Query(...),
    state: str = Query(...),
    shop: str = Query(...),
    db: Session = Depends(get_db)
):
    try:
        merchant = await complete_registration(db, state, code, shop)
        access_token = create_access_token(merchant.shopify_store_id)
        refresh_token = create_refresh_token(merchant.shopify_store_id)
        return success({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "merchant": {
                "id": merchant.id,
                "name": merchant.name,
                "email": merchant.email,
                "shopify_store_id": merchant.shopify_store_id
            }
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _generate_tokens(db: Session, username: str, password: str) -> dict:
    merchant = authenticate_merchant(db, username, password)
    if not merchant:
        raise HTTPException(status_code=401, detail="账号不存在/密码错误")
    return {
        "access_token": create_access_token(merchant.shopify_store_id),
        "refresh_token": create_refresh_token(merchant.shopify_store_id),
        "token_type": "bearer",
    }


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    return _generate_tokens(db, form_data.username, form_data.password)


@router.post("/login-json", include_in_schema=False)
def login_json(payload: LoginSchema, db: Session = Depends(get_db)):
    return _generate_tokens(db, payload.username, payload.password)


@router.post("/refresh")
def refresh_token(payload: RefreshRequest):
    try:
        data = decode_refresh_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="invalid_token")
        store_id = data.get("sub")
        if not store_id:
            raise HTTPException(status_code=401, detail="invalid_token")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid_token") from exc

    access_token = create_access_token(store_id)
    new_refresh_token = create_refresh_token(store_id)
    return success(
        {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }
    )
