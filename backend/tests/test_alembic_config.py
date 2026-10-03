import os
import subprocess
import sys
from pathlib import Path


def test_alembic_accepts_percent_encoded_database_password() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "postgresql+psycopg://travel:p%40ss@localhost:5432/travel"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(backend_dir / "src"), environment.get("PYTHONPATH", "")]
    )

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0004", "--sql"],
        cwd=backend_dir,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
