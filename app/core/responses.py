from typing import Any
from fastapi.encoders import jsonable_encoder


def success(data: Any = None, message: str = "success", code: int = 0) -> dict:
    return {"code": code, "message": message, "data": jsonable_encoder(data)}
