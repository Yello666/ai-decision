import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.auth_session import (
    is_refresh_jti_active,
    register_refresh_jti,
    revoke_refresh_jti,
)
from app.core.config import get_settings
from app.core.cookies import clear_auth_cookies, set_auth_cookies
from app.core.responses import success
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.db.mysql import get_db
from app.schemas.auth import LoginSchema, RefreshRequest  # noqa: F401  # 保留以兼容外部引用
from app.schemas.merchant import MerchantCreate
from app.services.auth_service import (
    authenticate_merchant,
    complete_registration,
    initiate_registration,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register")
async def register(payload: MerchantCreate):
    auth_url = await initiate_registration(payload)
    return success({"auth_url": auth_url})


async def _issue_tokens_for_store(store_id: str) -> Dict[str, str]:
    """签发 access + refresh，并在 Redis 登记 refresh jti（用于轮换/登出）。"""
    access_token = create_access_token(store_id)
    refresh_token, jti = create_refresh_token(store_id)
    await register_refresh_jti(store_id, jti)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.get("/shopify/callback")
async def shopify_callback(
    response: Response,
    code: str = Query(...),
    state: str = Query(...),
    shop: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        merchant = await complete_registration(db, state, code, shop)
        tokens = await _issue_tokens_for_store(merchant.shopify_store_id)
        set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])
        return success({
            **tokens,
            "merchant": {
                "id": merchant.id,
                "name": merchant.name,
                "email": merchant.email,
                "shopify_store_id": merchant.shopify_store_id,
            },
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
async def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """账号密码登录。成功后：
    - 下发 HttpOnly Cookie（主渠道）
    - 同时在响应体返回 token（兼容旧客户端的过渡期，后续可下线）
    """
    merchant = authenticate_merchant(db, form_data.username, form_data.password)
    if not merchant:
        raise HTTPException(status_code=401, detail="账号不存在/密码错误")

    tokens = await _issue_tokens_for_store(merchant.shopify_store_id)
    set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])
    return tokens


async def _read_refresh_token(request: Request) -> Optional[str]:
    """提取 refresh token：优先 cookie，降级读取 body.refresh_token。"""
    settings = get_settings()
    cookie_token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if cookie_token:
        return cookie_token
    try:
        body = await request.json()
    except Exception:
        return None
    if isinstance(body, dict):
        value = body.get("refresh_token")
        if isinstance(value, str) and value:
            return value
    return None


@router.post("/refresh")
async def refresh_token_endpoint(request: Request, response: Response):
    """刷新 access token。

    - 优先从 HttpOnly cookie 读取 refresh token；没有时回退读取 body（过渡期）。
    - 对 refresh token 做 ``jti`` 有效性校验（Redis 登记），并执行真正的轮换：
      撤销旧 jti，下发携带新 jti 的 refresh token。
    """
    token = await _read_refresh_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="missing_refresh_token")

    try:
        data = decode_refresh_token(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="invalid_token") from exc

    if data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="invalid_token")

    store_id = data.get("sub")
    jti = data.get("jti")
    if not store_id or not jti:
        # 旧版 refresh token 没有 jti：过渡期完成后此类 token 应失效，统一重新登录
        raise HTTPException(status_code=401, detail="invalid_token")

    if not await is_refresh_jti_active(store_id, jti):
        raise HTTPException(status_code=401, detail="token_revoked")

    access_token = create_access_token(store_id)
    new_refresh_token, new_jti = create_refresh_token(store_id)

    # 先登记新的，再撤销旧的，避免极端并发下的短暂失效
    await register_refresh_jti(store_id, new_jti)
    await revoke_refresh_jti(store_id, jti)

    set_auth_cookies(response, access_token, new_refresh_token)
    return success({
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    })


@router.post("/logout")
async def logout(request: Request, response: Response):
    """登出：

    - 若能从 cookie/body 解出 refresh token 的 jti，则撤销其 Redis 会话
    - 无论能否解出，都清理两个鉴权 cookie
    """
    token = await _read_refresh_token(request)
    if token:
        try:
            data = decode_refresh_token(token)
            if data.get("type") == "refresh":
                store_id = data.get("sub")
                jti = data.get("jti")
                if store_id and jti:
                    await revoke_refresh_jti(store_id, jti)
        except JWTError:
            # 过期 / 无效 refresh token：忽略即可，清 cookie 还是要做
            logger.debug("logout called with invalid refresh token; skipping revoke")

    clear_auth_cookies(response)
    return success({"message": "logged_out"})
