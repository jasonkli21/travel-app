#!/usr/bin/env bash
set -eu
lane_dir=$(cd "$(dirname "$0")/../../../.." && pwd)
harness="$lane_dir/travel/backend/verification/step4_travel"
pg_bin=${PAIR_PG_BIN:-/tmp/health-step2-venv/lib/python3.12/site-packages/pgserver/pginstall/bin}
pg_port=${PAIR_PG_PORT:-56672}
database_dir=$(mktemp -d /tmp/step4-travel-db.XXXXXX)
mkdir "$database_dir/socket"
"$pg_bin/initdb" -D "$database_dir/data" -A trust --username=travel_lane --no-locale --encoding=UTF8 > "$database_dir/init.log"
"$pg_bin/pg_ctl" -D "$database_dir/data" -l "$database_dir/postgres.log" -w start -o "-k $database_dir/socket -h 127.0.0.1 -p $pg_port"
trap '"$pg_bin/pg_ctl" -D "$database_dir/data" -m fast -w stop' EXIT
"$pg_bin/createdb" -h "$database_dir/socket" -p "$pg_port" -U travel_lane travel_pair
"$pg_bin/createdb" -h "$database_dir/socket" -p "$pg_port" -U travel_lane pas_pair
travel_dsn="postgresql+psycopg://travel_lane@/travel_pair?host=$database_dir/socket&port=$pg_port"
pas_dsn="postgresql://travel_lane@127.0.0.1:$pg_port/pas_pair"
pas="$lane_dir/pas"
python_path="$pas/backend:$pas/backend/src:$pas/reference-apps/src:$pas/sdk/python/src:$pas/adapters/mcp_source/src:$pas/adapters/mcp_context/src:$harness"
cd "$pas/backend"
env -i PATH="$PATH" APP_ENVIRONMENT=test AI_PROVIDER=fake AI_MODEL=fake \
  TRAVEL_PAIR_DISTINCT_OWNER="${PAIR_DISTINCT_OWNER:-0}" \
  TRAVEL_PAIR_EVIDENCE="${PAIR_EVIDENCE_DIR:-$harness/evidence}" \
  TRAVEL_PAIR_PG_BIN="$pg_bin" TRAVEL_PAIR_PG_PORT="$pg_port" \
  TRAVEL_PAIR_PG_PID="$(head -1 "$database_dir/data/postmaster.pid")" \
  TRAVEL_PAIR_PYTHON="${PAIR_TRAVEL_PYTHON:-/workspace/travel-app/backend/.venv/bin/python}" \
  TRAVEL_PAIR_TRAVEL_DSN="$travel_dsn" TRAVEL_PAIR_PAS_DSN="$pas_dsn" \
  PERSISTENCE_LOCAL_POSTGRES_DSN="$pas_dsn" PYTHONPATH="$python_path" \
  "${PAIR_PAS_PYTHON:-/tmp/pas-dispatch-venv/bin/python}" -B "$harness/run_pair.py"
