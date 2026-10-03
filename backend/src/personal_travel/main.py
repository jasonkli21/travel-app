from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from personal_travel.api.router import api_router
from personal_travel.api.schemas import ErrorBody, ErrorResponse
from personal_travel.config import get_settings
from personal_travel.services.errors import DomainError

settings = get_settings()

app = FastAPI(
    title="Personal Travel API",
    version="0.1.0",
    description="Authoritative travel-domain API for the personal travel application.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: object | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details if isinstance(details, dict) else None,
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


@app.exception_handler(DomainError)
async def handle_domain_error(_request: Request, exc: DomainError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def handle_request_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        422,
        "invalid_request",
        "The request could not be validated.",
        {"fields": jsonable_encoder(exc.errors())},
    )


@app.exception_handler(IntegrityError)
async def handle_integrity_error(_request: Request, _exc: IntegrityError) -> JSONResponse:
    return error_response(
        409,
        "integrity_error",
        "The requested change conflicts with existing travel data.",
    )
