from typing import Any


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def not_found(resource: str) -> DomainError:
    return DomainError(
        "not_found",
        f"The requested {resource} was not found.",
        status_code=404,
    )
