"""Synthetic Travel host built from the reviewed caller, source and journal."""

import base64
import json
import os
import sqlite3
import ssl
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
from alembic import command
from alembic.config import Config
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from personal_travel.api.routes.private_plan import mount_private_plan_source
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.private_plan import PrivatePlanTrust
from personal_travel.clients.private_dispatch import DispatchBinding, DispatchDestination, PrivateDispatchHTTP, SigningRole
from personal_travel.clients.private_plan import PlanDestination, PlanReceipt, PlanReceiptError, PlanReceiptRegistration
from personal_travel.config import Settings
from personal_travel.main import app as ordinary_app
from personal_travel.models.auth import AuthIdentity, AuthSession
from personal_travel.models.private_plan import PrivatePlanConfirmation
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError
from personal_travel.services.private_dispatch import (
    CreatedForDispatch, DispatchRegistration, LiveDispatchGrants, PreparedTravelDispatch,
    PrivateDispatchCaller, PrivateDispatchJournal, RegisteredTravelSource, source_commitment,
)
from personal_travel.services.private_plan import PrivatePlanJournal

from wire import encode, make_server, restrict_network, threaded


def public(hex_string):
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_string))


def receipt(body, signature):
    return PlanReceipt(body.encode(), signature)


def artifact(value):
    return [value.body.decode(), value.signature]


def prepared(value):
    return PreparedTravelDispatch(
        DispatchBinding.model_validate(value["binding"]),
        receipt(*value["create"]), receipt(*value["preview"]),
        value["envelope"].encode(), value["confirmation"].encode(),
    )


def now():
    return datetime.now(UTC)


def reply(response):
    return response.status_code, dict(response.headers), response.content


class TravelHost:
    def __init__(self, config):
        self.config = config
        self.settings = Settings(_env_file=None, personal_ai_plan_enabled=True)
        self.identity = config["identity"]
        self.session_digest = "c" * 64
        self.allocations = {}
        self.grants = {}
        self.checkpoint = Path(config["checkpoint"])
        with sqlite3.connect(self.checkpoint) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS submitted(conversation TEXT PRIMARY KEY, digest TEXT)"
            )
        self.expiry_offset = 0
        self.registration = None
        self.caller_options = None
        self.continuity_available = True
        trust = dict(config["trust"])
        trust["delegation_public_key"] = public(trust["delegation_public_key"])
        self.trust = PrivatePlanTrust(**trust)
        self.source_key = bytes.fromhex(config["source_key"])
        self.engine = create_engine(config["dsn"])
        migration = Config("alembic.ini", stdout=sys.stderr)
        migration.set_main_option("script_location", str(Path("migrations").resolve()))
        with self.engine.begin() as connection:
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0023"
        self.sessions = sessionmaker(self.engine, autoflush=False, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(AuthIdentity(**self.identity))
            session.add(AuthIdentity(**(self.identity | {
                "owner_id": "wrong-travel-owner", "subject": "wrong-subject-same-email",
            })))
            session.flush()
            session.add(AuthSession(
                token_hash=self.session_digest, owner_id=self.identity["owner_id"],
                csrf_token_hash="d" * 64, issued_at=now() - timedelta(seconds=1),
                expires_at=now() + timedelta(hours=2),
            ))
            for identifier, title in config["trips"].items():
                session.add(Trip(
                    id=UUID(identifier), owner_id=self.identity["owner_id"], title=title, revision=0,
                    start_date=date(2026, 11, 22), end_date=date(2026, 11, 23), timezone="UTC",
                ))
        service_key = public(config["service_key"])

        def verify_service(token, *, audience, expected_service_account):
            try:
                body, signature = map(base64.urlsafe_b64decode, token.split("."))
                service_key.verify(signature, body)
                if json.loads(body) != {"aud": audience, "account": expected_service_account}:
                    raise ValueError("wrong synthetic service identity")
            except InvalidSignature:
                raise ValueError("wrong synthetic service signature") from None

        self.app = FastAPI()
        mount_private_plan_source(
            self.app, settings=self.settings, trust=self.trust,
            sessions=self.sessions, verify_service=verify_service,
        )
        self.check = RegisteredTravelSource(
            self.journal, now, lambda cid, key: self.allocations.get((str(cid), str(key))),
        )

    def clock(self):
        return now() + timedelta(seconds=self.expiry_offset)

    def journal(self, *, identity=None):
        instant = int(now().timestamp())
        journal = PrivateDispatchJournal(
            PrivatePlanJournal(
                self.sessions(), settings=self.settings, trust=self.trust,
                principal=VerifiedPrincipal(
                    **(identity or self.identity), issued_at=instant - 2, expires_at=instant + 600,
                ),
                receipts=self.receipts, clock=self.clock,
            ),
            self.registration,
        )
        submit = journal.submit

        def retain_submission(**kwargs):
            result = submit(**kwargs)
            if result is not None:
                conversation = kwargs["prepared"].binding.conversation
                with sqlite3.connect(self.checkpoint) as connection:
                    connection.execute(
                        "INSERT INTO submitted VALUES (?,?)", (conversation, result.body_digest),
                    )
            return result

        journal.submit = retain_submission
        return journal

    def read_grants(self, _session, _trip, binding, _phase):
        return self.grants.get(binding.conversation)

    def continuity(self, _session, binding, submitted_digest):
        if not self.continuity_available:
            return False
        with sqlite3.connect(self.checkpoint) as connection:
            expected = connection.execute(
                "SELECT digest FROM submitted WHERE conversation = ?", (binding.conversation,),
            ).fetchone()
        return expected is None or expected == (submitted_digest,)

    def caller(self, journal, *, mode=None):
        pas = self.caller_options
        transport = PrivateDispatchHTTP(
            pas["origin"], bearer=pas["bearer"], tls=ssl.create_default_context(cafile=pas["cert"]),
        )
        if mode == "exit-after-commit":
            def exit_after_commit(*_):
                os._exit(79)
            transport.confirm_once = exit_after_commit
        if mode in {"revoke-after-status", "revoke-after-detail"}:
            if mode == "revoke-after-status":
                original = transport.dispatch_status
                def change_status(conversation, key):
                    value = original(conversation, key)
                    self.grants.pop(str(conversation))
                    return value
                transport.dispatch_status = change_status
            else:
                original = transport.dispatch_detail
                def change_detail(conversation, key):
                    value = original(conversation, key)
                    self.grants.pop(str(conversation))
                    return value
                transport.dispatch_detail = change_detail
        return PrivateDispatchCaller(journal, transport)

    def configure(self, **options):
        destination = DispatchDestination.model_validate(options["destination"])
        self.receipts = PlanReceiptRegistration(
            public(self.config["receipt_key"]),
            PlanDestination(
                application="travel", provider="ChatGPT", model=destination.model,
                runtime=f"local:{destination.runtime}", recipient=f"openai-responses:{destination.account}",
            ),
        )
        self.registration = DispatchRegistration(
            travel_owner=self.identity["owner_id"], pas_owner=options["owner"], pas_client=options["client"],
            tenant=options["tenant"], installation=options["installation"], destination=destination,
            preview_role=SigningRole(
                issuer="pas-fixture", audience="travel", kid="preview", public_key=public(options["preview_key"]),
            ),
            terminal_role=SigningRole(
                issuer="runtime-fixture", audience="pas-fixture", kid="terminal",
                public_key=public(options["terminal_key"]),
            ),
            source_key=self.source_key, session_token_digest=self.session_digest,
            role_active=lambda _: True, grants=self.read_grants, continuity=self.continuity,
        )
        self.caller_options = options

    def crash_worker(self, args):
        pid = os.fork()
        if pid == 0:
            self.engine.dispose(close=False)
            try:
                self.operate("confirm", args)
            except BaseException:
                import traceback

                traceback.print_exc()
                os._exit(78)
            os._exit(77)
        _, status = os.waitpid(pid, 0)
        return {"pid": pid, "exit_code": os.waitstatus_to_exitcode(status)}

    def operate(self, operation, args):
        if operation == "crash-worker":
            return self.crash_worker(args)
        if operation == "switch-db":
            self.engine.dispose()
            self.engine = create_engine(args["dsn"])
            self.sessions.configure(bind=self.engine)
            return None
        if operation == "configure":
            return self.configure(**args)
        if operation == "current":
            result = self.check.current(args["binding"].encode(), args["phase"], args["nonce"])
            if args["phase"] == "preview" and not result["current"]:
                incoming = json.loads(args["binding"])
                expected = self.allocations[incoming["conversation"], incoming["original_key"]].model_dump()
                differences = [k for k in expected if k != "preview_receipt" and incoming[k] != expected[k]]
                print(f"Preview allocation differing fields: {differences}", file=sys.stderr, flush=True)
            return result
        if operation == "clock":
            self.expiry_offset = args["offset"]
            return None
        if operation == "revoke-source":
            self.grants.pop(args["conversation"])
            return None
        if operation == "grant":
            grants = LiveDispatchGrants(
                permission_epoch=1, policy=args["policy"], branch=args["branch"], memory=args["memory"],
                source_grant="travel-trip-read", model_grant="travel-fake-model",
                runtime_grant="travel-fake-local", destination=self.registration.destination,
                expires=args["expires"],
            )
            self.grants[args["conversation"]] = grants
            with self.sessions() as session:
                trip = session.get(Trip, UUID(args["trip"]))
                return source_commitment(self.source_key, trip, grants)
        if operation == "seed-trip":
            with self.sessions.begin() as session:
                session.add(Trip(
                    id=UUID(args["trip"]), owner_id=self.identity["owner_id"],
                    title=args["title"], revision=0,
                    start_date=date(2026, 11, 22), end_date=date(2026, 11, 23), timezone="UTC",
                ))
            return None
        if operation == "trip-owner":
            with self.sessions.begin() as session:
                row = session.get(Trip, UUID(args["trip"]))
                row.owner_id = self.identity["owner_id"] if args["current"] else "wrong-travel-owner"
            return None
        if operation == "row":
            with self.sessions() as session:
                row = session.scalar(select(PrivatePlanConfirmation).where(
                    PrivatePlanConfirmation.original_key == UUID(args["original_key"])
                ))
                if row is None:
                    return None
                return {"attempt": str(row.attempt), "root": str(row.root), "body_sha256": row.body_digest,
                        "body": bytes(row.body).decode(), "quarantined": row.quarantined_at is not None}
        identity = None
        if args.get("wrong_owner"):
            identity = self.identity | {"owner_id": "wrong-travel-owner", "subject": "wrong-subject-same-email"}
        journal = self.journal(identity=identity)
        with journal.session:
            mode = args.get("mode")
            caller = self.caller(journal, mode=mode)
            trip = UUID(args["trip"])
            if operation == "create":
                result = caller.create(trip_id=trip, create_key=UUID(args["key"]), title=args["title"])
                return {"conversation": str(result.conversation), "create": artifact(result.receipt)}
            if operation == "preview":
                binding = DispatchBinding.model_validate(args["binding"])
                self.allocations[binding.conversation, binding.original_key] = binding
                result = caller.preview(
                    trip_id=trip, created=CreatedForDispatch(
                        UUID(binding.conversation), receipt(*args["create"]),
                    ),
                    original_key=UUID(binding.original_key), content=args["content"], expected=binding,
                )
                if result is None:
                    return None
                return {
                    "binding": result.binding.model_dump(), "create": artifact(result.create_receipt),
                    "preview": artifact(result.preview_receipt), "envelope": result.envelope.decode(),
                    "confirmation": result.confirmation_body.decode(),
                }
            binding = DispatchBinding.model_validate(args["prepared"]["binding"])
            if operation == "confirm":
                if mode == "exit-before-commit":
                    from sqlalchemy import event

                    def exit_before_commit(session):
                        assert any(isinstance(row, PrivatePlanConfirmation) for row in session.new)
                        session.flush()
                        os._exit(79)

                    event.listen(journal.session, "before_commit", exit_before_commit)
                candidate = prepared(args["prepared"])
                result = caller.confirm(
                    trip_id=trip, prepared=candidate,
                    human_confirmation=args.get("confirmation", candidate.confirmation_body.decode()).encode(),
                )
            elif operation == "observe":
                result = caller.observe(trip_id=trip, binding=binding)
            else:
                raise AssertionError(operation)
            return asdict(result) | {"message_id": str(result.message_id) if result.message_id else None}


def main():
    denied = restrict_network()
    config = json.loads(Path(sys.argv[1]).read_bytes())
    host = TravelHost(config)
    with TestClient(host.app) as app, TestClient(ordinary_app, base_url="http://localhost") as default:
        ordinary = {
            "health": default.get("/health").status_code,
            "source": default.post("/internal/personal-ai/plan-source", json={}).status_code,
        }

        def reverse(method, path, headers, body):
            return reply(app.request(method, path, headers=headers, content=body))

        with threaded(make_server(0, reverse, cert=config["cert"], key=config["tls_key"])) as reverse_port:
            def control(_verb, path, _headers, body):
                if path == "/_fixture/network-audit":
                    return 200, {}, encode(denied)
                try:
                    result = host.operate(path.removeprefix("/_fixture/"), json.loads(body))
                except DomainError as error:
                    result = {"error": error.status_code, "code": error.code}
                except (PlanReceiptError, httpx.HTTPError) as error:
                    result = {"error": type(error).__name__, "detail": str(error)}
                return 200, {}, encode(result)

            local = make_server(0, control)
            print(encode({"pid": os.getpid(), "reverse": reverse_port, "port": local.server_port,
                          "ordinary": ordinary}).decode(), flush=True)
            local.serve_forever()


if __name__ == "__main__":
    main()
