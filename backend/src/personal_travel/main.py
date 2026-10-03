import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from personal_travel.api.errors import error_response
from personal_travel.api.middleware import LocalBoundaryMiddleware
from personal_travel.api.router import api_router
from personal_travel.config import get_settings
from personal_travel.services.errors import DomainError

settings = get_settings()

# Configure only our safe application logs. Enabling root HTTP-client logging
# would expose provider URLs, which may contain credentials or private queries.
application_logger = logging.getLogger("personal_travel")
if not application_logger.handlers:
    application_logger.addHandler(logging.StreamHandler())
application_logger.setLevel(logging.INFO)
application_logger.propagate = False

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

app.add_middleware(
    LocalBoundaryMiddleware,
    hosts=settings.allowed_host_list,
    origins=settings.cors_origin_list,
)
app.include_router(api_router)


@app.exception_handler(DomainError)
async def handle_domain_error(_request: Request, exc: DomainError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def handle_request_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        422,
        "invalid_request",
        "The request could not be validated.",
        {
            "fields": [
                {"location": list(error["loc"]), "type": error["type"], "message": error["msg"]}
                for error in exc.errors()
            ]
        },
    )


@app.exception_handler(IntegrityError)
async def handle_integrity_error(_request: Request, _exc: IntegrityError) -> JSONResponse:
    return error_response(
        409,
        "integrity_error",
        "The requested change conflicts with existing travel data.",
    )


@app.exception_handler(SQLAlchemyError)
async def handle_database_error(_request: Request, _exc: SQLAlchemyError) -> JSONResponse:
    return error_response(503, "database_unavailable", "Travel storage is temporarily unavailable.")


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(Exception)
async def handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
    # Do not log exception text: database/provider exceptions can contain private
    # records, SQL parameters, queries or API keys. Request IDs support diagnosis.
    request_id = _request.scope.get("request_id", "unavailable")
    logging.getLogger("personal_travel.errors").error(
        "request id=%s error_type=%s", request_id, type(_exc).__name__
    )
    response = error_response(
        500, "internal_error", "The travel service could not complete the request."
    )
    response.headers["X-Request-ID"] = str(request_id)
    response.headers["Cache-Control"] = "no-store"
    return response
