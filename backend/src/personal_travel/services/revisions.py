from personal_travel.services.errors import DomainError


def require_expected_revision(
    current_revision: int,
    expected_revision: int | None,
    *,
    aggregate: str,
) -> None:
    if expected_revision is None or expected_revision == current_revision:
        return
    raise DomainError(
        "stale_revision",
        f"The {aggregate} changed after it was loaded. Reload before editing again.",
        status_code=409,
        details={
            "aggregate": aggregate,
            "expected_revision": expected_revision,
            "current_revision": current_revision,
        },
    )
