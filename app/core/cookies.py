"""HttpOnly 鉴权 Cookie 下发 / 清理助手。

统一管理 access_token 与 refresh_token 两个 Cookie 的属性，保持应用各处一致：
- ``HttpOnly``：JS 无法读取，降低 XSS 盗 token 风险
- ``Secure`` + ``SameSite``：根据 ``settings.COOKIE_SECURE``/``COOKIE_SAMESITE``
- ``Path=/``：全站有效
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Response

from app.core.config import get_settings


def _common_cookie_attrs() -> Dict[str, Any]:
    settings = get_settings()
    attrs: Dict[str, Any] = {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "path": "/",
    }
    if settings.COOKIE_DOMAIN:
        attrs["domain"] = settings.COOKIE_DOMAIN
    return attrs


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
) -> None:
    """把 access/refresh token 写入 HttpOnly Cookie。"""
    settings = get_settings()
    common = _common_cookie_attrs()

    response.set_cookie(
        key=settings.ACCESS_COOKIE_NAME,
        value=access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **common,
    )
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    """登出时清理两个鉴权 Cookie。"""
    settings = get_settings()
    common = _common_cookie_attrs()
    # httponly 不是 delete_cookie 的必要参数，但 path/domain/secure/samesite 必须和 set 时一致
    delete_kwargs = {
        "path": common["path"],
        "secure": common["secure"],
        "samesite": common["samesite"],
        "httponly": common["httponly"],
    }
    if "domain" in common:
        delete_kwargs["domain"] = common["domain"]

    response.delete_cookie(settings.ACCESS_COOKIE_NAME, **delete_kwargs)
    response.delete_cookie(settings.REFRESH_COOKIE_NAME, **delete_kwargs)
