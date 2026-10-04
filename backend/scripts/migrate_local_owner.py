"""Dry-run or explicitly migrate the local owner graph to a verified identity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID, uuid4

from personal_travel.auth.owner_migration import (
    OwnerMigrationRejected,
    apply_owner_migration,
    inspect_owner_migration,
)
from personal_travel.config import get_settings
from personal_travel.db.session import SessionFactory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-owner-id", default=get_settings().owner_id)
    parser.add_argument("--target-owner-id", required=True)
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-digest")
    parser.add_argument("--confirmation")
    parser.add_argument("--backup-file")
    parser.add_argument("--backup-sha256")
    return parser


def main() -> int:
    args = _parser().parse_args()
    run_id = args.run_id or uuid4()
    try:
        with SessionFactory() as session:
            if args.apply:
                required = (
                    args.expected_plan_digest,
                    args.confirmation,
                    args.backup_file,
                    args.backup_sha256,
                    args.run_id,
                )
                if not all(required):
                    raise OwnerMigrationRejected(
                        "Apply requires the inspected plan digest, run ID, source/target "
                        "confirmation, and a backup file plus SHA-256."
                    )
                plan = apply_owner_migration(
                    session,
                    source_owner_id=args.source_owner_id,
                    target_owner_id=args.target_owner_id,
                    run_id=run_id,
                    expected_plan_digest=args.expected_plan_digest,
                    backup_file=Path(args.backup_file),
                    expected_backup_sha256=args.backup_sha256,
                    confirmation=args.confirmation,
                )
                payload = {"mode": "applied", "run_id": str(run_id), **plan.as_dict()}
            else:
                with session.begin():
                    plan = inspect_owner_migration(
                        session,
                        source_owner_id=args.source_owner_id,
                        target_owner_id=args.target_owner_id,
                    )
                payload = {"mode": "dry_run", "run_id": str(run_id), **plan.as_dict()}
        print(json.dumps(payload, sort_keys=True, indent=2))
        return 0 if plan.can_apply else 2
    except OwnerMigrationRejected as error:
        print(json.dumps({"error": str(error)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
