from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from personal_travel.db.session import get_session


def session_dependency() -> Iterator[Session]:
    yield from get_session()


def owner_dependency(request: Request) -> str:
    principal = request.scope.get("principal")
    owner_id = getattr(principal, "owner_id", None)
    if not isinstance(owner_id, str) or not owner_id:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return owner_id


SessionDependency = Annotated[Session, Depends(session_dependency)]
OwnerDependency = Annotated[str, Depends(owner_dependency)]
ExpectedRevision = Annotated[
    int | None,
    Header(alias="X-Expected-Revision", ge=0),
]
