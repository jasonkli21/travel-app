from __future__ import annotations

import pytest

from scripts.recovery_smoke import (
    _repository_alembic_head,
    _require_repository_head,
)
from scripts.secure_backup import BackupError


def test_recovery_smoke_requires_the_repository_migration_head() -> None:
    current_head = _repository_alembic_head()
    _require_repository_head(current_head)

    with pytest.raises(BackupError, match="current head"):
        _require_repository_head("stale-revision")
