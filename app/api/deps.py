from typing import Mapping, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db.mysql import get_db
from app.services.merchant_service import get_merchant_by_store_id

# auto_error=False：允许请求不带 Authorization 头，改由 cookie 提供 access token。
# tokenUrl 保持不变，供 OpenAPI / Swagger 调试沿用旧的 OAuth2 流程。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def extract_access_token_from_cookies(
    cookies: Mapping[str, str],
    fallback_token: Optional[str] = None,
) -> Optional[str]:
    """按优先级提取 access token：Cookie → fallback（Bearer，供 REST 使用）。

    REST 传入 ``request.cookies``；WebSocket 在路由层单独使用 ``?access_token=``，不经由此函数。
    """
    settings = get_settings()
    cookie_token = cookies.get(settings.ACCESS_COOKIE_NAME)
    return cookie_token or fallback_token


def _extract_access_token(request: Request, bearer_token: Optional[str]) -> Optional[str]:
    """委托给 :func:`extract_access_token_from_cookies`。"""
    return extract_access_token_from_cookies(request.cookies, bearer_token)


def get_current_merchant(
    request: Request,
    bearer_token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    token = _extract_access_token(request, bearer_token)
    if not token:
        raise HTTPException(status_code=401, detail="not_authenticated")

    try:
        payload = decode_access_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="invalid_token")
        store_id = payload.get("sub")
        if not store_id:
            raise HTTPException(status_code=401, detail="invalid_token")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="invalid_token") from exc

    merchant = get_merchant_by_store_id(db, store_id)
    if not merchant:
        raise HTTPException(status_code=401, detail="merchant_not_found")
    if not merchant.is_active:
        raise HTTPException(status_code=403, detail="merchant_inactive")
    return merchant
