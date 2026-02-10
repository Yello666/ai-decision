from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session
from app.core.security import decode_access_token
from app.db.mysql import get_db
from app.services.merchant_service import get_merchant_by_store_id

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_merchant(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
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
