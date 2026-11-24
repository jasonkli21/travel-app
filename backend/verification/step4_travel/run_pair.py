"""Exercise the reviewed PAS and Travel dispatch paths against separate processes."""

import base64
import functools
import hmac
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock
from uuid import UUID, uuid4, uuid5

import httpx
import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from personal_ai.api.private_application_plan import PROFILE_PREFIX
from personal_ai.auth import verification
from personal_ai.auth.application_clients import client_binding_namespace
from personal_ai.context.contracts import fingerprint
from personal_ai.contracts.context_references import ApplicationPlanSourceGrant
from personal_ai.contracts.private_dispatch import Binding, MEDIA_TYPE, ORIGINAL_KEY_HEADER, Destination, canonical, wire_digest
from personal_ai.entities.conversation import Message, MessageRole, MessageStatus
from personal_ai.local_bridge.private_dispatch import LoopbackReceiver, RuntimeDispatch, fence_identity
from personal_ai.main import create_app
from personal_ai.persistence.postgres import PostgresDatabase
from personal_ai.persistence.postgres.inference_policy import PostgresInferencePolicy
from personal_ai.persistence.postgres.routing_observations import PostgresRoutingDecisionRepository
from personal_ai.services.external_turns.application_plan import PlanAttempts
from personal_ai.services.external_turns.dispatch_journal import SyntheticDispatchJournal
from personal_ai.services.external_turns.private_dispatch import (
    CreateBinding, CurrentSource, DispatchRegistration, PrivateDispatch,
)
from personal_ai.settings import Settings, get_settings
from personal_ai_reference_apps.context.travel_plan import CURRENT_FIELDS, TravelPlanSource, TravelPlanSourceClient
from tests.app_context_support.application_plan import ApplicationPlanFixture
from tests.test_travel_plan_paired import (
    MEMORY, PROMPT, TITLES, TRIPS, bearer_factory, make_pas_settings, setup_pas,
)

from wire import cert_pair, encode, exchange, make_server, restrict_network, rpc, threaded

HERE = Path(__file__).resolve().parent
TRAVEL_BACKEND = HERE.parent.parent
PAS_BACKEND = Path.cwd()
TARGET = "isolated-pas-preview"
TENANT = "step4-travel-fixture"
INSTALLATION = str(UUID(int=452))
SOURCE_KEY = b"synthetic step4 travel key bytes only"
MEMORY_KEY = b"synthetic step4 memory key bytes only"
RESULT = "Paired synthetic itinerary response."
TRAVEL_PYTHON = os.environ["TRAVEL_PAIR_PYTHON"]
GATES = {"mode": None, "offset": 0}
PAS_CUTS = {"after-root", "after-independent-claim", "after-send-start"}


def instant():
    return datetime.now(UTC)


def public(key):
    return key.public_key().public_bytes_raw().hex()


@contextmanager
def child(args, env, work, stderr):
    with stderr.open("w") as errors:
        process = subprocess.Popen(
            args, cwd=work, env=env | {"PATH": os.environ["PATH"]},
            stdout=subprocess.PIPE, stderr=errors, text=True,
        )
        try:
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("worker startup failed: " + stderr.read_text())
            yield json.loads(line)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


def start_default_pas():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, app_environment="test", ai_provider="fake", ai_model="fake",
        auth_mode="development", auth_required=False,
    )
    with TestClient(app) as client:
        return {
            "health": client.get("/health").status_code,
            "create": client.post(PROFILE_PREFIX, json={}).status_code,
            "preview": client.post(f"{PROFILE_PREFIX}/{uuid4()}/context-preview", json={}).status_code,
            "continue": client.post(
                f"{PROFILE_PREFIX}/{uuid4()}/continue",
                headers={"content-type": MEDIA_TYPE}, content=b"{}",
            ).status_code,
        }


def main():
    denied = restrict_network()
    logs = Path(os.environ["TRAVEL_PAIR_EVIDENCE"]).resolve()
    logs.mkdir(exist_ok=True, parents=True)
    work = Path(tempfile.mkdtemp(prefix="step4-travel-runtime-", dir="/dev/shm"))
    with ExitStack() as stack:
        monkey = stack.enter_context(MonkeyPatch.context())
        database = PostgresDatabase(os.environ["TRAVEL_PAIR_PAS_DSN"], environment="test")
        stack.callback(database.close)
        assert database.migrate()
        ordinary = start_default_pas()
        assert ordinary == {"health": 200, "create": 404, "preview": 404, "continue": 404}
        now = instant()
        settings = make_pas_settings(work, now)
        bearer = bearer_factory(monkey, now)
        principal = verification.principal_from_google_token(bearer(), settings)
        namespace = client_binding_namespace(principal, "travel")
        delegation_key = Ed25519PrivateKey.generate()
        service_key = Ed25519PrivateKey.generate()
        receipt_key = Ed25519PrivateKey.generate()
        preview_key = Ed25519PrivateKey.generate()
        release_key = Ed25519PrivateKey.generate()
        terminal_key = Ed25519PrivateKey.generate()
        trust = {
            "target_id": TARGET, "client_namespace": namespace,
            "service_audience": "https://fixture-travel.invalid",
            "service_account": "paired-fixture@example.invalid",
            "delegation_issuer": "paired-pas-fixture", "delegation_key_id": "paired-fixture",
            "delegation_public_key": public(delegation_key),
        }
        statement = encode({"aud": trust["service_audience"], "account": trust["service_account"]})
        service_token = ".".join(base64.urlsafe_b64encode(part).decode() for part in (
            statement, service_key.sign(statement),
        ))
        travel_cert, travel_tls_key = cert_pair(work, "travel")
        pas_cert, pas_tls_key = cert_pair(work, "pas")
        separate_owner = os.environ.get("TRAVEL_PAIR_DISTINCT_OWNER") == "1"
        travel_owner = "travel-owner-step4-distinct" if separate_owner else principal.owner_id
        config = work / "travel.json"
        config.write_bytes(encode({
            "cert": travel_cert, "tls_key": travel_tls_key,
            "dsn": os.environ["TRAVEL_PAIR_TRAVEL_DSN"], "trust": trust,
            "checkpoint": str(work / "travel-independent-submit.sqlite"),
            "source_key": SOURCE_KEY.hex(), "service_key": public(service_key), "receipt_key": public(receipt_key),
            "identity": {"owner_id": travel_owner, "issuer": principal.issuer,
                         "subject": principal.subject, "email": principal.email},
            "trips": dict(zip(TRIPS, TITLES, strict=True)),
        }))
        config.chmod(0o600)
        travel = stack.enter_context(child(
            [TRAVEL_PYTHON, "-B", str(HERE / "travel_host.py"), str(config)],
            {"PYTHONPATH": str(TRAVEL_BACKEND / "src")},
            TRAVEL_BACKEND, logs / "travel-process.log",
        ))
        assert travel["ordinary"] == {"health": 200, "source": 404}, travel
        receiver = stack.enter_context(child(
            [sys.executable, "-B", str(HERE / "wire.py"), "receiver"], {},
            HERE, logs / "receiver-process.log",
        ))
        call = functools.partial(rpc, travel["port"])
        grants = {}
        dependencies = {}
        source_digests = {}
        creates = {}
        preview_requests = {}
        callback = []
        requests = []
        issued = []
        covered = {}
        capture_lock = Lock()
        cuts = {}

        def grant(binding, *, purpose, **_):
            return grants.get((binding.conversation_id, purpose))

        source = TravelPlanSource(
            TravelPlanSourceClient(
                base_url=f"https://127.0.0.1:{travel['reverse']}",
                audience=trust["service_audience"], issuer=trust["delegation_issuer"],
                key_id=trust["delegation_key_id"], signing_key=delegation_key,
                service_token=lambda **_: service_token,
                transport=httpx.HTTPTransport(verify=travel_cert, trust_env=False, retries=0),
            ), grant, grant, grant,
        )
        original_onboard = ApplicationPlanFixture.onboard
        with MonkeyPatch.context() as patch:
            patch.setattr(ApplicationPlanFixture, "onboard", lambda self, *a, **kw: original_onboard(
                self, *a, **kw, model="fake", maximum_sensitivity="restricted",
            ))
            pas, fixture, target = setup_pas(
                work, settings, now, source, principal, namespace, bearer, database, receipt_key,
            )
        root_witness = work / "pas-independent-claim.sqlite"
        with sqlite3.connect(root_witness) as connection:
            connection.execute("CREATE TABLE witnesses(root TEXT PRIMARY KEY, owner TEXT, attempt TEXT, payload TEXT)")

        def record_claim(root, owner, attempt, payload):
            if GATES["mode"] == "after-root":
                os._exit(79)
            with sqlite3.connect(root_witness) as connection:
                connection.execute("INSERT INTO witnesses VALUES (?,?,?,?)", (str(root), owner, str(attempt), payload))

        def claim_current(root, owner, attempt, payload):
            with sqlite3.connect(root_witness) as connection:
                row = connection.execute("SELECT owner,attempt,payload FROM witnesses WHERE root = ?", (str(root),)).fetchone()
            return row == (owner, str(attempt), payload)

        def continuity():
            with sqlite3.connect(root_witness) as connection:
                witnesses = set(connection.execute("SELECT root,owner,attempt,payload FROM witnesses"))
            with database.transaction() as connection:
                canonical_roots = set(connection.execute(
                    "SELECT root_id::text,owner_id,attempt_id::text,payload_sha256 "
                    "FROM inference_route_roots WHERE state='fenced'"
                ).fetchall())
            return witnesses == canonical_roots

        roots = PostgresInferencePolicy(database)
        root_observations = PostgresRoutingDecisionRepository(database)
        fixture.service.attempts = PlanAttempts(
            root_observations, roots, recovery_current=lambda: True,
            record_claim=record_claim, claim_current=claim_current,
        )
        scope = target.plan.scope
        paired = target.bridge.installation
        destination = Destination(
            account=paired.registration_binding, connection=str(paired.connection_id),
            endpoint="loopback-fixture", model="fake", provider="fake", revision=paired.connection_revision,
            runtime=str(paired.runtime_id),
        )
        runtime = RuntimeDispatch(
            destination=destination,
            receiver=LoopbackReceiver(receiver["port"], Ed25519PublicKey.from_public_bytes(bytes.fromhex(receiver["key"]))),
            pas_issuer="pas-fixture", release_kid="release", release_key=release_key.public_key(),
            issuer="runtime-fixture", terminal_kid="terminal", terminal_key=terminal_key,
        )
        memory = hmac.digest(MEMORY_KEY, canonical([
            str(target.memory.id), wire_digest(MEMORY.encode()), fixture.disclosure_epochs[scope],
        ]), "sha256").hex()

        def commitments(frozen):
            snapshots = {item.provider_id: item for item in frozen.snapshots}
            assert set(snapshots) == {"ai_memory", source.spec.provider_id}
            assert any(
                dependency.entity_ref is not None and dependency.entity_ref.entity_id == str(target.memory.id)
                for dependency in snapshots["ai_memory"].permission_dependencies
            )
            dependencies[str(frozen.original_key)] = frozen
            return source_digests[frozen.binding.conversation_id], memory

        def expired_at(receipt, frozen):
            cid = frozen.binding.conversation_id
            domain = next(item for item in frozen.snapshots if item.provider_id == source.spec.provider_id)
            receipt_key.public_key().verify(bytes.fromhex(receipt), canonical({
                "schema_version": "travel-application-plan-receipt-v1", "operation": "preview",
                "target_id": TARGET, "client_namespace": namespace, "application_id": "travel",
                "workspace_id": None, "owner_id": principal.owner_id, "issuer": principal.issuer,
                "subject": principal.subject, "conversation_id": str(cid),
                "request_digest": wire_digest(preview_requests[cid, frozen.original_key]),
                "response_digest": wire_digest(frozen.attested_body),
                "source_revision": domain.source_version,
            }))
            return int(datetime.fromisoformat(json.loads(frozen.attested_body)["expires_at"]).timestamp())

        def current(binding, phase, nonce):
            value = call("current", binding=binding.decode(), phase=phase, nonce=nonce)
            callback.append({"phase": phase, "current": value["current"], "nonce": nonce})
            return value

        registered = DispatchRegistration(
            target.plan, TENANT, INSTALLATION, runtime, CurrentSource(current),
            verified_create=lambda cid, _key: creates.get(cid),
            source_commitments=commitments, verified_preview_expiry=expired_at,
            load_dependencies=lambda binding: dependencies.get(binding.original_key),
            trust_current=lambda: True,
        )
        journal = SyntheticDispatchJournal(work / "pas-journal")
        dispatcher = PrivateDispatch(
            plans=fixture.service, journal=journal, registrations=(registered,),
            issuer="pas-fixture", preview_kid="preview", preview_key=preview_key, release_key=release_key,
            continuity_current=continuity,
            clock=lambda: int(instant().timestamp()) + GATES["offset"],
        )
        for route in pas.state.private_application_plan_routes:
            pas.router.routes.remove(route)
        pas.state.private_application_plan_routes = ()
        fixture.mount(travel_receipt_key=receipt_key, dispatch=dispatcher)
        client = stack.enter_context(TestClient(pas, raise_server_exceptions=True))
        policy = pas.state.application_registry.get("travel").context_policy.version
        original_send = http.client.HTTPConnection.request

        def measure_provider(connection, method, path, body=None, headers=None, **kwargs):
            if (connection.port, method, path) == (receiver["port"], "POST", "/dispatch"):
                issued.append({"attempt": headers["X-PAS-Attempt"], "sha256": wire_digest(body), "bytes": len(body)})
            value = original_send(connection, method, path, body, headers or {}, **kwargs)
            if (connection.port, method, path) == (receiver["port"], "POST", "/dispatch") and GATES["mode"] == "fence-first-revoke":
                call("revoke-source", conversation=GATES["conversation"])
            return value

        monkey.setattr(http.client.HTTPConnection, "request", measure_provider)
        original_mark = target.store.mark_send_started

        def mark_started(*args, **kwargs):
            if GATES["mode"] == "after-independent-claim":
                os._exit(79)
            if GATES["mode"] == "revoke-before-fence":
                call("revoke-source", conversation=GATES["conversation"])
            return original_mark(*args, **kwargs)

        monkey.setattr(target.store, "mark_send_started", mark_started)
        original_journal_mark = journal.mark_started

        def journal_start(*args, **kwargs):
            value = original_journal_mark(*args, **kwargs)
            if GATES["mode"] == "after-send-start":
                os._exit(79)
            return value

        monkey.setattr(journal, "mark_started", journal_start)

        def pas_request(method, path, headers, body):
            if method == "POST" and path.endswith("/context-preview"):
                preview_requests[UUID(path.split("/")[-2]), UUID(json.loads(body)["idempotency_key"])] = body
            mode = GATES["mode"]
            if method == "POST" and path.endswith("/continue") and mode in PAS_CUTS and mode not in cuts:
                pid = os.fork()
                if pid == 0:
                    # Replace objects that own threads or live connections in the parent.
                    database._pool = None
                    database._async_pool = None
                    registered.current._slot = Lock()
                    registered.current._worker = ThreadPoolExecutor(max_workers=1)
                    try:
                        reached = TestClient(pas, raise_server_exceptions=True).request(
                            method, path, headers=headers, content=body,
                        )
                        print(f"PAS cut did not fire: {mode}, HTTP {reached.status_code}", file=sys.stderr, flush=True)
                    except BaseException:
                        import traceback

                        traceback.print_exc()
                        os._exit(78)
                    os._exit(77)
                _, child_status = os.waitpid(pid, 0)
                cut = {"pid": pid, "exit_code": os.waitstatus_to_exitcode(child_status)}
                cuts[mode] = cut
                with capture_lock:
                    requests.append({"method": method, "path": path, "status": None, "worker": cut})
                return None, {}, b""
            response = client.request(method, path, headers=headers, content=body)
            if path == PROFILE_PREFIX and response.status_code == 201:
                cid = UUID(response.json()["conversation_id"])
                request = json.loads(body)
                signature = response.headers["x-application-plan-receipt"]
                receipt_key.public_key().verify(bytes.fromhex(signature), canonical({
                    "schema_version": "travel-application-plan-receipt-v1", "operation": "create",
                    "target_id": TARGET, "client_namespace": namespace, "application_id": "travel",
                    "workspace_id": None, "owner_id": principal.owner_id, "issuer": principal.issuer,
                    "subject": principal.subject, "conversation_id": str(cid),
                    "request_digest": wire_digest(body), "response_digest": wire_digest(response.content),
                    "source_revision": None,
                }))
                creates[cid] = CreateBinding(
                    key=UUID(request["idempotency_key"]), receipt=signature, epoch=1,
                    expires=int((instant() + timedelta(seconds=120)).timestamp()),
                )
            with capture_lock:
                requests.append({"method": method, "path": path, "status": response.status_code})
            return response.status_code, dict(response.headers), response.content

        pas_port = stack.enter_context(threaded(make_server(
            0, pas_request, cert=pas_cert, key=pas_tls_key,
        )))
        call(
            "configure", origin=f"https://127.0.0.1:{pas_port}", bearer=bearer(), cert=pas_cert,
            owner=principal.owner_id, client=namespace, tenant=TENANT, installation=INSTALLATION,
            destination=destination.model_dump(), preview_key=public(preview_key), terminal_key=public(terminal_key),
        )

        def pas_http(method, path, *, body=b"", token=None, original_key=None):
            headers = {
                "authorization": f"Bearer {token or bearer()}", "x-application-id": "travel",
                "accept": MEDIA_TYPE, "content-type": MEDIA_TYPE,
            }
            if original_key:
                headers[ORIGINAL_KEY_HEADER] = original_key
            return exchange(pas_port, method, path, ca=pas_cert, body=body, headers=headers)

        def pending(label, index=0):
            # Isolate scenarios from the separate R4 preview metadata rate cap.
            # Durable dispatch rows, original keys, dependencies, and fences remain intact.
            with fixture.service._lock:
                fixture.service._previews.clear()
            trip = TRIPS[index] if label == "success" else str(uuid5(UUID(TRIPS[index]), label))
            if label != "success":
                call("seed-trip", trip=trip, title=TITLES[index])
            create_key = uuid4()
            original = uuid4()
            created = call("create", trip=trip, key=str(create_key), title=f"Paired fixture {label}")
            assert "conversation" in created, (label, created)
            cid = UUID(created["conversation"])
            bound = target.plan.preview_binding(principal, cid)
            for purpose, recipient in (
                ("source-read", bound.target_id),
                ("runtime-handoff", bound.runtime_recipient),
                ("inference-disclosure", bound.model_recipient),
            ):
                grants[cid, purpose] = ApplicationPlanSourceGrant(
                    binding=bound, purpose=purpose, recipient=recipient,
                    fields=CURRENT_FIELDS, version="1", expires_at=instant() + timedelta(seconds=120),
                )
            branch = fingerprint([], include_state=True)
            source_digests[cid] = call(
                "grant", trip=trip, conversation=str(cid), policy=policy, branch=branch,
                memory=memory, expires=int(instant().timestamp()) + 120,
            )
            scope_key = dispatcher.key(registered, cid, original)
            root = uuid5(cid, "pas-dispatch/1:" + scope_key)
            binding = {
                "app": "travel", "workspace": None, "actor_issuer": principal.issuer, "subject": principal.subject,
                "owner": principal.owner_id, "client": namespace, "tenant": TENANT, "installation": INSTALLATION,
                "destination": destination.model_dump(), "epoch": 1, "target": TARGET, "conversation": str(cid),
                "create_key": str(create_key), "create_receipt": wire_digest(creates[cid].receipt.encode()),
                "original_key": str(original), "root": str(root), "attempt": str(uuid5(root, "foreground")),
                "branch": branch, "memory": memory, "policy": policy, "source": source_digests[cid],
                "preview_receipt": "0" * 64,
            }
            preview = call("preview", trip=trip, binding=binding, create=created["create"], content=PROMPT)
            assert "confirmation" in preview, (label, preview, requests[-4:], callback[-5:])
            return {"trip": trip, "prepared": preview}

        def cursors():
            return len(issued), len(rpc(receiver["port"], "arrivals")), len(requests)

        def confirm(item, **kw):
            return call("confirm", **(item | kw))

        def observe(item, **kw):
            return call("observe", **(item | kw))

        def row(item):
            binding = item["prepared"]["binding"]
            return call("row", original_key=binding["original_key"], conversation=binding["conversation"])

        def paths(item):
            binding = item["prepared"]["binding"]
            cid, key = binding["conversation"], binding["original_key"]
            return f"{PROFILE_PREFIX}/{cid}/continue", f"/v1/conversations/{cid}/continuations/{key}", f"/v1/conversations/{cid}"

        def durable(item):
            binding = Binding.model_validate(item["prepared"]["binding"])
            key = dispatcher.key(registered, UUID(binding.conversation), UUID(binding.original_key))
            attempt = journal.read(key)
            identity, original, fingerprint = fence_identity(binding, attempt.body_hash, attempt.payload_hash)
            with database.transaction() as connection:
                root = roots.root(connection, UUID(binding.root), binding.owner)
            witness = claim_current(UUID(binding.root), binding.owner, UUID(binding.attempt), attempt.payload_hash)
            with target.store.transaction() as tx:
                receipt = tx.state.receipts.get(identity)
            return {
                "state": attempt.state, "body_sha256": attempt.body_hash, "attempt": binding.attempt,
                "terminal": attempt.terminal is not None, "root": str(binding.root),
                "native_root_state": root[4] if root else None, "independent_witness": witness,
                "runtime_state": receipt.status if receipt else None,
                "retained_fence": target.store.retained_fence_current(identity, original, fingerprint),
            }

        def snapshot(db_name, prefix):
            pg_bin = Path(os.environ["TRAVEL_PAIR_PG_BIN"])
            pg_port = os.environ["TRAVEL_PAIR_PG_PORT"]
            native = f"postgresql://travel_lane@127.0.0.1:{pg_port}/"
            archive = work / f"{prefix}.pgdump"
            restored = native + prefix
            subprocess.run([
                pg_bin / "pg_dump", "-Fc", "--no-owner", "-f", archive, "-d", native + db_name,
            ], check=True, timeout=10)
            with psycopg.connect(native + "postgres", autocommit=True) as connection:
                connection.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(prefix)))
            subprocess.run([
                pg_bin / "pg_restore", "--no-owner", "--exit-on-error", "-d", restored, archive,
            ], check=True, timeout=10)
            return restored, {"database": prefix, "archive_bytes": archive.stat().st_size}

        def record(label, start, *, expected, **evidence):
            received = rpc(receiver["port"], "arrivals")[start[1]:]
            new_issued = issued[start[0]:]
            assert (len(new_issued), len(received)) == expected, (label, new_issued, received)
            assert all({k: value[k] for k in sent} == sent for sent, value in zip(new_issued, received, strict=True))
            if "preview_payload_sha256" in evidence:
                assert new_issued[0]["sha256"] == evidence["preview_payload_sha256"]
            for event in received:
                body = event.pop("body")
                assert MEMORY in body and PROMPT in body and TITLES[0] in body and TITLES[1] not in body
            covered[label] = {"issued": new_issued, "received": received, "pas_http": requests[start[2]:], **evidence}
            output = logs / "paired-observations.json"
            output.write_bytes(json.dumps({
                "pas": {"pid": os.getpid(), "port": pas_port, "ordinary": ordinary},
                "travel": travel, "receiver": {"pid": receiver["pid"], "port": receiver["port"]},
                "native_postgresql": {"pid": int(os.environ["TRAVEL_PAIR_PG_PID"]),
                                      "port": int(os.environ["TRAVEL_PAIR_PG_PORT"]), "version": "16.2"},
                "native_pas": {"routing_roots": "inference_route_roots", "principal_and_memory": "PostgreSQL"},
                "native_travel": {"schema": "0023", "journal": "private_plan_confirmations"},
                "runtime_fixture_dir": str(work),
                "network": "numeric loopback, pinned TLS in both app directions, Travel PostgreSQL Unix socket, PAS PostgreSQL IPv4 loopback, receiver HTTP",
                "provider_totals": {"issued": len(issued), "received": len(rpc(receiver["port"], "arrivals"))},
                "python_blocked_egress_attempts": {"pas": denied, "travel": call("network-audit"),
                                                   "receiver": rpc(receiver["port"], "network-audit")},
                "observations": covered, "source_callbacks": callback,
            }, indent=2).encode() + b"\n")
            print(label, expected, evidence, flush=True)

        if separate_owner:
            baseline = cursors()
            rejection = call("create", trip=TRIPS[0], key=str(uuid4()), title="Distinct opaque IDs")
            assert rejection == {"code": "not_found", "error": 404}, rejection
            assert requests[-1] == {"method": "POST", "path": PROFILE_PREFIX, "status": 201}
            assert principal.owner_id != travel_owner
            record("legacy-create-distinct-opaque-owner", baseline, expected=(0, 0),
                   result=rejection, pas_create_status=201, opaque_owner_ids_equal=False)
        else:
            run_cases(**locals())
        assert denied == call("network-audit") == rpc(receiver["port"], "network-audit") == []
        for path in ("travel-process.log", "receiver-process.log"):
            assert "FIXTURE_NETWORK_EGRESS_BLOCKED" not in (logs / path).read_text()


def run_cases(**objects):
    pending = objects["pending"]
    cursors = objects["cursors"]
    confirm = objects["confirm"]
    observe = objects["observe"]
    row = objects["row"]
    paths = objects["paths"]
    record = objects["record"]
    pas_http = objects["pas_http"]
    bearer = objects["bearer"]
    call = objects["call"]
    target = objects["target"]
    fixture = objects["fixture"]
    grants = objects["grants"]
    receiver = objects["receiver"]
    durable = objects["durable"]

    def literal_confirmation(item):
        return item["prepared"]["confirmation"].encode()

    def conversation(item):
        return item["prepared"]["binding"]["conversation"]

    def provider_counts():
        return cursors()[:2]

    def assert_no_resend(item, observation):
        anchor = cursors()
        assert confirm(item) == observation
        calls = objects["requests"][anchor[2]:]
        assert calls and all(request["method"] == "GET" for request in calls), calls
        assert provider_counts() == anchor[:2]


    start = cursors()
    item = pending("success")
    assert cursors()[:2] == start[:2]
    assert row(item) is None
    post, status_path, detail_path = paths(item)
    assert confirm(item)["state"] == "unknown"
    submitted = row(item)
    assert submitted["body"] == item["prepared"]["confirmation"]
    assert wire_digest(submitted["body"].encode()) == submitted["body_sha256"]
    observation = observe(item)
    assert observation["state"] == "completed" and observation["result"] == RESULT, observation
    result = pas_http("GET", status_path)
    status = json.loads(result[2])
    binding = item["prepared"]["binding"]
    assert status["attempt"] == submitted["attempt"] == binding["attempt"]
    assert status["root"] == submitted["root"] == binding["root"]
    assert status["body_sha256"] == submitted["body_sha256"]
    assert status["terminal"]["message_id"] == observation["message_id"]
    assert status["terminal"]["result_sha256"] == wire_digest(RESULT.encode())
    assert_no_resend(item, observation)
    assert pas_http("GET", detail_path, original_key=binding["original_key"])[0] == 200
    assert pas_http("GET", detail_path, original_key=str(uuid4()))[0] == 404
    assert pas_http("GET", status_path, token=bearer(subject="wrong-subject-same-email"))[0] in (401, 403, 404)
    assert pas_http("GET", status_path, token=bearer("owner-control"))[0] in (401, 403, 404)
    assert observe(item, trip=TRIPS[1])["error"] == 404
    assert observe(item, wrong_owner=True)["error"] == 404
    record("success-current-get", start, expected=(1, 1), state="completed", receipt=status["terminal"],
           confirmation_sha256=submitted["body_sha256"], terminal_signature=status["signature"],
           committed_at=result[1].get("x-pas-committed-at"), durable=durable(item),
           preview_payload_sha256=json.loads(item["prepared"]["envelope"])["claims"]["payload_sha256"])

    before = cursors()
    call("trip-owner", trip=TRIPS[0], current=False)
    assert observe(item)["error"] == 404
    assert pas_http("GET", status_path)[0] == 404
    assert pas_http("GET", detail_path, original_key=binding["original_key"])[0] == 404
    call("trip-owner", trip=TRIPS[0], current=True)
    record("revoked-current-get", before, expected=(0, 0), state="withheld", denial=404)

    for failure in ("acl", "source", "pas-source", "memory", "branch", "model", "recipient", "expiry", "confirmation"):
        initial = cursors()
        candidate = pending("denial-" + failure)
        cid = UUID(conversation(candidate))
        complete = target.plan
        disclosures = fixture.disclosures[complete.scope]
        post, _, _ = paths(candidate)
        explicit_confirmation = literal_confirmation(candidate)

        if failure == "acl":
            call("trip-owner", trip=candidate["trip"], current=False)
        elif failure == "source":
            call("revoke-source", conversation=str(cid))
        elif failure == "pas-source":
            grants.pop((cid, "inference-disclosure"))
        elif failure == "memory":
            fixture.disclosures[complete.scope] = ()
        elif failure == "branch":
            message = Message(
                id=uuid4(), conversation_id=cid, owner_id=complete.scope.owner_id,
                application_id="travel", role=MessageRole.USER, status=MessageStatus.COMPLETED,
                content="Paired synthetic branch mutation", created_at=instant(),
            )
            fixture.host.messages._messages[message.id] = message
        elif failure == "model":
            object.__setattr__(complete, "model", "unexpected-synthetic-model")
        elif failure == "recipient":
            object.__setattr__(complete, "scope", complete.scope.model_copy(update={"model_recipient": "wrong-recipient"}))
        elif failure == "expiry":
            call("clock", offset=70)
        elif failure == "confirmation":
            altered = json.loads(explicit_confirmation)
            altered["confirmation"] = "0" * 64
            explicit_confirmation = encode(altered)

        observed = confirm(candidate, confirmation=explicit_confirmation.decode())
        last = [request for request in objects["requests"][initial[2]:] if request["path"] == post]
        if failure in {"pas-source", "memory", "branch", "model", "recipient"}:
            assert observed["state"] == "unknown", (failure, observed)
            assert len(last) == 1 and last[0]["status"] in (404, 409, 410, 503), (failure, last)
        else:
            assert observed["error"] in (404, 409, 410), (failure, observed)
            assert last == [] and row(candidate) is None, (failure, last)
        record("denial-" + failure, initial, expected=(0, 0), result=observed)

        if failure == "acl":
            call("trip-owner", trip=candidate["trip"], current=True)
        elif failure == "memory":
            fixture.disclosures[complete.scope] = disclosures
        elif failure in {"model", "recipient"}:
            if failure == "model":
                object.__setattr__(complete, "model", "fake")
            else:
                object.__setattr__(complete, "scope", complete.runtime._bridge.installation.scope)
        elif failure == "expiry":
            call("clock", offset=0)

    initial = cursors()
    candidate = pending("same-key-concurrent")
    post, _, _ = paths(candidate)
    ready = Barrier(2)
    def concurrently():
        ready.wait()
        return confirm(candidate)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(concurrently) for _ in range(2)]
        concurrently_observed = [future.result() for future in futures]
    stored = row(candidate)
    assert stored["attempt"] == candidate["prepared"]["binding"]["attempt"]
    assert stored["body"] == candidate["prepared"]["confirmation"]
    requests = [request for request in objects["requests"][initial[2]:] if request["path"] == post]
    assert len(requests) == 1, requests
    observed = observe(candidate)
    sent = provider_counts()[0] - initial[0]
    assert sent in (0, 1)
    state = durable(candidate)
    assert state["attempt"] == stored["attempt"]
    if sent == 1:
        assert observed["state"] == "completed", (observed, concurrently_observed, requests)
        assert state["body_sha256"] == stored["body_sha256"]
    else:
        assert observed["state"] == "unknown" and observed["result"] is None
        assert not state["terminal"] and state["body_sha256"] in (None, stored["body_sha256"])
    assert_no_resend(candidate, observed)
    record("same-key-concurrent", initial, expected=(sent, sent), result=observed,
           callers=concurrently_observed, durable=state, committed_travel_body=stored["body_sha256"])

    initial = cursors()
    candidate = pending("travel-before-commit")
    post, _, _ = paths(candidate)
    cut = call("crash-worker", **candidate, mode="exit-before-commit")
    assert cut["exit_code"] == 79, cut
    assert row(candidate) is None
    assert all(request["path"] != post for request in objects["requests"][initial[2]:])
    record("travel-before-commit", initial, expected=(0, 0), journal_row=False,
           interruption="Travel worker exited after native row flush before commit", worker=cut)

    initial = cursors()
    candidate = pending("travel-after-commit")
    post, _, _ = paths(candidate)
    cut = call("crash-worker", **candidate, mode="exit-after-commit")
    assert cut["exit_code"] == 79, cut
    assert row(candidate)["body"] == candidate["prepared"]["confirmation"]
    observed = observe(candidate)
    assert observed["state"] in {"unknown", "quarantined"} and observed["result"] is None
    replay = confirm(candidate)
    assert replay.get("state") in {"unknown", "quarantined"} or replay.get("error") == 404
    assert all(request["path"] != post for request in objects["requests"][initial[2]:])
    record("travel-after-commit-before-pas", initial, expected=(0, 0), journal_row=True,
           observation=observed, replay=replay, interruption="Travel worker exited after commit before confirmation transport", worker=cut)

    for mode, expected_state in (
        ("after-independent-claim", "claimed"), ("after-send-start", "send_started"),
    ):
        initial = cursors()
        candidate = pending(mode)
        GATES["mode"] = mode
        try:
            post, _, detail = paths(candidate)
            assert confirm(candidate)["state"] == "unknown"
            cut = objects["cuts"][mode]
            assert cut["exit_code"] == 79, cut
            assert any(request["path"] == post and request["status"] is None for request in objects["requests"][initial[2]:])
            state = durable(candidate)
            assert state["state"] == expected_state and state["terminal"] is False
            assert state["native_root_state"] == "fenced" and state["independent_witness"] is True
            assert state["runtime_state"] == ("send_started" if mode == "after-send-start" else "claimed_pre_send")
            assert state["retained_fence"] == (mode == "after-send-start")
            observed = observe(candidate)
            assert observed["state"] == expected_state and observed["result"] is None
            assert_no_resend(candidate, observed)
            assert pas_http("POST", post, body=literal_confirmation(candidate))[0] == 202
            assert pas_http("GET", detail, original_key=candidate["prepared"]["binding"]["original_key"])[0] == 404
            record(mode, initial, expected=(0, 0), observation=observed, durable=state, worker=cut)
        finally:
            GATES["mode"] = None

    for outcome in ("disconnect", "wrong-attempt"):
        initial = cursors()
        candidate = pending("receiver-" + outcome)
        rpc(receiver["port"], "outcome", mode=outcome)
        try:
            post, _, detail = paths(candidate)
            assert confirm(candidate)["state"] == "unknown"
            assert any(request["path"] == post and request["status"] == 503 for request in objects["requests"][initial[2]:])
            observed = observe(candidate)
            assert observed["state"] == "send_started" and observed["result"] is None
            assert_no_resend(candidate, observed)
            assert pas_http("POST", post, body=literal_confirmation(candidate))[0] == 202
            assert pas_http("GET", detail, original_key=candidate["prepared"]["binding"]["original_key"])[0] == 404
            record("receiver-" + outcome, initial, expected=(1, 1), observation=observed, durable=durable(candidate))
        finally:
            rpc(receiver["port"], "outcome", mode="completed")

    for mode, expected in (("revoke-before-fence", (0, 0)), ("fence-first-revoke", (1, 1))):
        initial = cursors()
        candidate = pending(mode)
        GATES.update(mode=mode, conversation=conversation(candidate))
        try:
            post, status_path, detail = paths(candidate)
            assert confirm(candidate)["state"] == "unknown"
            assert observe(candidate)["error"] == 404
            assert pas_http("GET", status_path)[0] == 404
            assert pas_http("GET", detail, original_key=candidate["prepared"]["binding"]["original_key"])[0] == 404
            assert pas_http("POST", post, body=literal_confirmation(candidate))[0] in (404, 503)
            state = durable(candidate)
            assert state["retained_fence"] == (mode == "fence-first-revoke")
            assert state["terminal"] is False
            record(mode, initial, expected=expected, state="withheld", durable=state)
        finally:
            GATES["mode"] = None

    for mode in ("revoke-after-status", "revoke-after-detail"):
        initial = cursors()
        candidate = pending(mode)
        assert confirm(candidate)["state"] == "unknown"
        record(mode + "-baseline-completion", initial, expected=(1, 1), durable=durable(candidate))
        initial = cursors()
        observed = observe(candidate, mode=mode)
        assert observed["state"] == "unknown" and observed["result"] is None, observed
        assert row(candidate)["quarantined"] is True
        record(mode, initial, expected=(0, 0), observation=observed, travel_quarantined=True)

    candidate = pending("restore-older-stores")
    snapshot = objects["snapshot"]
    pas_restore_dsn, pas_backup = snapshot("pas_pair", "pas_pre_confirmation")
    travel_restore_dsn, travel_backup = snapshot("travel_pair", "travel_pre_confirmation")
    travel_restore_dsn = travel_restore_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    journal = objects["journal"]
    retired = SyntheticDispatchJournal(objects["work"] / "older-pas-journal")
    with sqlite3.connect(journal._path) as live, sqlite3.connect(retired._path) as old:
        live.backup(old)
    initial = cursors()
    assert confirm(candidate)["state"] == "unknown"
    assert observe(candidate)["state"] == "completed"
    record("restore-baseline-completed", initial, expected=(1, 1), durable=durable(candidate))

    initial = cursors()
    try:
        call("switch-db", dsn=travel_restore_dsn)
        assert row(candidate) is None
        assert observe(candidate)["error"] == 404
        assert confirm(candidate)["error"] == 503
        assert durable(candidate)["retained_fence"] is True
        record("restored-older-travel-postgresql", initial, expected=(0, 0), backup=travel_backup,
               observation="current checkpoint denied send; restored original journal row absent",
               retained_pas=durable(candidate))
    finally:
        call("switch-db", dsn=os.environ["TRAVEL_PAIR_TRAVEL_DSN"])

    initial = cursors()
    database = objects["database"]
    dispatcher = objects["dispatcher"]
    try:
        database.close()
        database._dsn = pas_restore_dsn
        dispatcher.journal = retired
        binding = candidate["prepared"]["binding"]
        original = dispatcher.key(objects["registered"], UUID(binding["conversation"]), UUID(binding["original_key"]))
        restored_row = retired.read(original)
        assert restored_row.state == "unknown" and restored_row.body_hash is None and restored_row.terminal is None
        assert objects["continuity"]() is False
        before = durable(candidate)
        assert before["native_root_state"] is None and before["independent_witness"] is True
        assert before["retained_fence"] is True
        post, status_path, _ = paths(candidate)
        assert observe(candidate)["state"] == "unknown"
        assert confirm(candidate)["state"] == "unknown"
        assert pas_http("GET", status_path)[0] == 503
        assert pas_http("POST", post, body=literal_confirmation(candidate))[0] == 503
        record("restored-older-pas-postgresql-and-sqlite", initial, expected=(0, 0), backup=pas_backup,
               restored_dispatch_journal_bytes=retired._path.stat().st_size,
               restored_dispatch_state=restored_row.state,
               observation="missing native root disagreed with independent claim and runtime fence",
               retained=before)
    finally:
        database.close()
        database._dsn = os.environ["TRAVEL_PAIR_PAS_DSN"]
        dispatcher.journal = journal

    initial = cursors()
    candidate = pending("after-native-pas-root-before-witness")
    GATES["mode"] = "after-root"
    try:
        post, status_path, _ = paths(candidate)
        assert confirm(candidate)["state"] == "unknown"
        cut = objects["cuts"]["after-root"]
        assert cut["exit_code"] == 79, cut
        assert observe(candidate)["state"] == "unknown"
        assert pas_http("GET", status_path)[0] == 503
        assert pas_http("POST", post, body=literal_confirmation(candidate))[0] == 503
        state = durable(candidate)
        assert state["state"] == "claimed" and state["native_root_state"] == "fenced"
        assert state["independent_witness"] is False and state["runtime_state"] is None
        record("after-native-pas-root-before-witness", initial, expected=(0, 0), state="unavailable",
               durable=state, worker=cut)
    finally:
        GATES["mode"] = None


if __name__ == "__main__":
    main()
