from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from personal_travel.config import get_settings

settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_timeout=5,
    connect_args={
        "connect_timeout": settings.database_connect_timeout_seconds,
        "options": (
            f"-c statement_timeout={settings.database_statement_timeout_ms} "
            f"-c lock_timeout={settings.database_lock_timeout_ms}"
        ),
    },
)
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionFactory() as session:
        yield session
