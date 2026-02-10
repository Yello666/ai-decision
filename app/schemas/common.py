from typing import Any, Optional
from pydantic import BaseModel


class ResponseModel(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[Any] = None
