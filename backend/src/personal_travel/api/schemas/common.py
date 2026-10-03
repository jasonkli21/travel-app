from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

LocalTime = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
    Field(description="Local wall-clock time on the owning trip day, formatted as HH:MM."),
]


LOCAL_TIME_DESCRIPTION = (
    "Local wall-clock time on the owning trip day, formatted as HH:MM; "
    "null means the item is untimed."
)


def _trim_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Safe human-readable error message.")
    details: dict[str, object] | None = Field(
        default=None,
        description="Optional structured details safe for clients to display or inspect.",
    )


class ErrorResponse(BaseModel):
    error: ErrorBody


COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "model": ErrorResponse,
        "description": "The requested resource was not found for the configured owner.",
    },
    409: {
        "model": ErrorResponse,
        "description": "The requested change conflicts with existing travel data.",
    },
    422: {
        "model": ErrorResponse,
        "description": "The request body or domain values are invalid.",
    },
}
