"""Offline signed reverse-hop contract tests with separate fabricated keys."""

import base64
import json
import time
from dataclasses import dataclass, field, replace
from unittest import TestCase
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.datastructures import Headers

from personal_travel.api.schemas.private_plan import PrivatePlanSourceRequest
from personal_travel.auth.private_plan import PrivatePlanTrust, authenticate_private_plan_source
from personal_travel.clients.task_wire import sign_delegated_user

OWNER = "fabricated-owner"
SUBJECT = "fabricated-subject"
CONVERSATIONS = (UUID(int=101), UUID(int=102))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode()


@dataclass
class SignedPlanFixture:
    service_key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    human_key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)

    @property
    def trust(self) -> PrivatePlanTrust:
        return PrivatePlanTrust(
            target_id="isolated-pas-preview",
            client_namespace="c" * 64,
            service_audience="https://travel.invalid/internal/personal-ai/plan-source",
            service_account="fabricated-private-plan@test.iam.gserviceaccount.com",
            delegation_issuer="fabricated-pas-private-plan-delegation",
            delegation_key_id="fabricated-private-plan-key",
            delegation_public_key=self.human_key.public_key(),
        )

    def body(self, conversation_id: UUID = CONVERSATIONS[0]) -> dict[str, object]:
        return {
            "schema_version": "travel-private-plan-source-v1",
            "purpose": "personal_ai_context",
            "destination_application_id": "travel",
            "workspace_id": None,
            "lane": "user_owned_plan",
            "target_id": self.trust.target_id,
            "client_namespace": self.trust.client_namespace,
            "conversation_id": str(conversation_id),
            "operation": "current",
        }

    def verify_service(self, token: str, *, audience: str, expected_service_account: str) -> None:
        """Verify fabricated service credentials independently of human delegation."""
        try:
            encoded, signature = token.split(".")
            body = base64.urlsafe_b64decode(encoded)
            self.service_key.public_key().verify(base64.urlsafe_b64decode(signature), body)
            claims = json.loads(body)
            if claims != {"aud": audience, "account": expected_service_account}:
                raise ValueError
        except (ValueError, InvalidSignature):
            raise ValueError("fabricated_service_denied") from None

    def headers(
        self,
        body: dict[str, object],
        *,
        age: int = 0,
        subject: str = SUBJECT,
        service_audience: str | None = None,
        delegation_audience: str | None = None,
    ) -> dict[str, str]:
        trust = self.trust
        service = json.dumps(
            {
                "aud": service_audience or trust.service_audience,
                "account": trust.service_account,
            }
        ).encode()
        now = int(time.time()) - age
        return {
            "Authorization": f"Bearer {_b64(service)}.{_b64(self.service_key.sign(service))}",
            "X-User-Delegation": sign_delegated_user(
                request_body=body,
                private_key=self.human_key,
                issuer=trust.delegation_issuer,
                audience=delegation_audience or trust.service_audience,
                key_id=trust.delegation_key_id,
                user_issuer=trust.human_issuer,
                user_subject=subject,
                authenticated_at=now,
                now=now,
                lifetime_seconds=30,
            ),
        }

    def authenticate(self, body: dict[str, object], headers: dict[str, str]) -> str:
        return authenticate_private_plan_source(
            Headers(headers),
            body,
            PrivatePlanSourceRequest.model_validate(body),
            self.trust,
            self.verify_service,
        ).subject


class TestPrivatePlanDelegation(TestCase):
    def test_rejects_incomplete_registration(self) -> None:
        trust = SignedPlanFixture().trust
        for name in (
            "service_audience",
            "service_account",
            "delegation_issuer",
            "delegation_key_id",
            "human_issuer",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                replace(trust, **{name: ""})

    def setUp(self) -> None:
        self.fixture = SignedPlanFixture()
        self.body = self.fixture.body()

    def test_requires_independent_service_and_human_signatures(self) -> None:
        self.assertEqual(
            self.fixture.authenticate(self.body, self.fixture.headers(self.body)), SUBJECT
        )
        foreign = SignedPlanFixture()
        for name in ("Authorization", "X-User-Delegation"):
            with self.subTest(header=name), self.assertRaises(ValueError):
                forged = self.fixture.headers(self.body) | {name: foreign.headers(self.body)[name]}
                self.fixture.authenticate(self.body, forged)
        for option in ("service_audience", "delegation_audience"):
            with self.subTest(audience=option), self.assertRaises(ValueError):
                headers = self.fixture.headers(self.body, **{option: "https://another.invalid"})
                self.fixture.authenticate(self.body, headers)

    def test_does_not_carry_browser_or_forwarded_identity(self) -> None:
        for header in ("Cookie", "Origin", "X-User-ID-Token", "X-Owner-ID"):
            with self.subTest(header=header), self.assertRaises(ValueError):
                self.fixture.authenticate(
                    self.body, self.fixture.headers(self.body) | {header: "fabricated"}
                )

    def test_rejects_changed_conversation_operation_and_expired_assertion(self) -> None:
        for changed in (
            self.fixture.body(CONVERSATIONS[1]),
            self.body | {"operation": "verify", "expected_source_version": "0"},
        ):
            with self.subTest(body=changed), self.assertRaises(ValueError):
                self.fixture.authenticate(changed, self.fixture.headers(self.body))
        with self.assertRaises(ValueError):
            self.fixture.authenticate(self.body, self.fixture.headers(self.body, age=90))

    def test_denies_trip_selectors_and_wrong_registration_even_if_signed(self) -> None:
        for changed in (
            {"lane": "automatic"},
            {"target_id": "another-target"},
            {"client_namespace": "b" * 64},
            {"workspace_id": str(UUID(int=1))},
            {"entity_refs": [{"entity_type": "trip", "entity_id": str(UUID(int=1))}]},
            {"operation": "verify"},
            {"purpose": "shopping_trip_context", "destination_application_id": "shopping"},
        ):
            with self.subTest(change=changed), self.assertRaises(ValueError):
                body = self.body | changed
                self.fixture.authenticate(body, self.fixture.headers(body))
