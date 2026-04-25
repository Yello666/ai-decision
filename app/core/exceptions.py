from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


def http_exception_handler(_: Request, exc) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.detail, "data": None},
    )


def validation_exception_handler(_: Request, exc) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "message": "validation_error",
            "data": jsonable_encoder(exc.errors()),
        },
    )
