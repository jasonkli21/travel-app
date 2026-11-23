"""Conformance to the untouched neutral pas-dispatch/1 golden transcripts."""

import base64
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from personal_travel.clients.private_dispatch import (
    MEDIA_TYPE,
    DispatchBinding,
    DispatchEvidenceConflict,
    PrivateDispatchHTTP,
    SigningRole,
    canonical,
    digest,
    parse,
    verify_detail,
    verify_preview,
    verify_status,
)
from personal_travel.clients.private_plan import PlanReceiptError

FIXTURES = Path(__file__).parent / "fixtures" / "pas_dispatch_v1"


def signing_role(issuer, kid, audience, key):
    return SigningRole(
        issuer,
        kid,
        audience,
        Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(key + "=")),
    )


def transcript(app):
    def raw(name):
        return (FIXTURES / app / name).read_bytes()

    pas, runtime = parse(raw("keys.json"))
    binding = DispatchBinding.model_validate(parse(raw("binding.json")))
    claims = parse(raw("preview.json"))
    confirmation = parse(raw("confirm.json"))
    terminal_claims = parse(raw("terminal.json"))
    return (
        raw,
        binding,
        claims,
        confirmation,
        terminal_claims,
        signing_role("pas-fixture", "p1", app, pas),
        signing_role("runtime-fixture", "r1", "pas-fixture", runtime),
    )


@pytest.mark.parametrize("app", ["travel", "health"])
def test_frozen_transcript(app):
    raw, binding, claims, confirmation, terminal, preview_role, terminal_role = transcript(app)
    envelope = canonical({"claims": claims, "signature": confirmation["preview_sig"]})
    verified, expires = verify_preview(envelope, binding, preview_role, 1000)
    assert expires == 1060
    assert canonical(verified.model_dump()) == raw("confirm.json")
    assert digest(raw("payload.json")) == claims["payload_sha256"]
    assert digest(raw("payload.json") + b" ") != claims["payload_sha256"]
    status = verify_status(
        canonical(
            {
                "state": "completed",
                "original_key": binding.original_key,
                "attempt": binding.attempt,
                "root": binding.root,
                "body_sha256": digest(raw("confirm.json")),
                "message_id": terminal["message_id"],
                "terminal": terminal,
                "signature": raw("terminal.sig").decode("ascii"),
            }
        ),
        binding,
        digest(raw("confirm.json")),
        terminal_role,
        1000,
    )
    detail = {
        "message_id": status.message_id,
        "attempt": binding.attempt,
        "root": binding.root,
        "original_key": binding.original_key,
        "result": "ok",
    }
    assert verify_detail(canonical(detail), status).result == "ok"
    with pytest.raises(DispatchEvidenceConflict):
        verify_detail(canonical(detail | {"result": "wrong"}), status)
    with pytest.raises(ValueError):
        verify_preview(envelope, binding, preview_role, 1060)
    with pytest.raises(ValueError):
        verify_preview(envelope, binding.model_copy(update={"epoch": 2}), preview_role, 1000)


def test_reject_cross_profile_noncanonical_and_unknown_version():
    _, binding, claims, confirmation, _, role, _ = transcript("travel")
    _, _, _, health, _, _, _ = transcript("health")
    with pytest.raises(ValueError):
        verify_preview(
            canonical({"claims": claims, "signature": health["preview_sig"]}), binding, role, 1000
        )
    with pytest.raises(ValueError):
        verify_preview(
            canonical(
                {
                    "claims": claims | {"v": "pas-dispatch/2"},
                    "signature": confirmation["preview_sig"],
                }
            ),
            binding,
            role,
            1000,
        )
    for raw in (b'{"x":1,"x":1}', b'{"b":1, "a":2}', b'{"x":1.0}', b"\xef\xbb\xbf{}", b"{}\n"):
        with pytest.raises(ValueError):
            parse(raw)
    # RFC 8785 sorts member names by UTF-16 code units.
    assert canonical({"\ue000": 1, "\U00010000": 2}) == '{"𐀀":2,"":1}'.encode()


def test_confirmation_transport_sends_same_body_once_without_following_redirects():
    raw, _, _, _, _, _, _ = transcript("travel")
    seen = []

    def receiver(request):
        seen.append(request)
        return httpx.Response(307, headers={"location": "https://example.invalid/elsewhere"})

    client = PrivateDispatchHTTP(
        "https://127.0.0.1:8080",
        bearer="fixture-pas-token",
        transport=httpx.MockTransport(receiver),
    )
    from uuid import UUID

    with pytest.raises(PlanReceiptError):
        client.confirm_once(UUID(int=1), raw("confirm.json"))
    assert len(seen) == 1
    assert seen[0].content == raw("confirm.json")
    assert seen[0].headers["content-type"] == MEDIA_TYPE
    assert seen[0].headers["authorization"] == "Bearer fixture-pas-token"
    with pytest.raises(ValueError):
        PrivateDispatchHTTP(
            "https://example.invalid",
            bearer="fixture-pas-token",
            transport=httpx.MockTransport(receiver),
        )
