from doctest import Example

from pydantic import BaseModel, Field


class LoginSchema(BaseModel):
    username: str = Field(..., alias="email",example="string")
    password: str =Field(example="1234567890")

    class Config:
        allow_population_by_field_name = True

#
# class TokenResponse(BaseModel):
#     access_token: str
#     refresh_token: str
#     token_type: str = "bearer"

#
class RefreshRequest(BaseModel):
    refresh_token: str
