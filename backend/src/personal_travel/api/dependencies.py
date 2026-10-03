from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from personal_travel.config import Settings, get_settings
from personal_travel.db.session import get_session


def session_dependency() -> Iterator[Session]:
    yield from get_session()


def owner_dependency(settings: Annotated[Settings, Depends(get_settings)]) -> str:
    return settings.owner_id


SessionDependency = Annotated[Session, Depends(session_dependency)]
OwnerDependency = Annotated[str, Depends(owner_dependency)]
