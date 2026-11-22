# 0017 · Travel source binding for an isolated PAS preview

**Status:** Proposed cross-repository contract for offline review. Travel's
preview-only candidate implements this boundary. Integrated preview and dispatch
remain `BLOCKED_CONTRACT`.

| Paired input | Revision |
| --- | --- |
| Travel product | `61a708b257cb9862bc415ce8a7151201489d3472` |
| PAS product | `f5f60fb6f871dad9d59071f2ebf91ecb8e1c69f1` |
| PAS probe evidence | `d66963b49dc5de8802510e7449a9a9ac6cb7fcd8` |

The PAS private plan body does not take a trip selector. The Travel v2 source
accepts an authorized owner's selected trip and receives no conversation
authority. One human can own two trips, so owner access alone does not
establish which trip belongs to a PAS conversation. The paired probe also
observed that the PAS planner omits Travel without a candidate reference and
that the private preview rejects the unmodified Travel source revision.
[Paired probe](https://github.com/jasonkli21/personal-ai-system/blob/d66963b49dc5de8802510e7449a9a9ac6cb7fcd8/docs/context-inference-integration/evidence/2026-11-22-travel-plan-preview-contract.md)

## Travel ownership and journal

Travel records the original create UUID and title, the selected trip, the verified
issuer and subject, the Travel owner, and its registered PAS target and client.
The binding is initially pending. Only a verified create result from that same
target, client and user can complete it. The caller carries the verified PAS
receipt into the internal binding service. The binding uses the existing
conversation anchor primary key with a distinct `user_owned_plan` lane, so
concurrent automatic and private claims cannot both succeed. A repeated create
must match the persisted title and original key within its owner, target and
client scope. Continuation keys are scoped to their individual bindings. The
application and workspace are fixed to `travel` and `null` throughout.

One preview intent is available per bound conversation in this slice. The
journal commits the original UUID, a digest of the exact PAS preview request,
and the source revision before a caller can submit preview. The first caller
receives the reservation. A concurrent identical request joins it. A changed
key, request or source revision conflicts. When a validated preview receipt is
available, the journal can retain its digest and expiry. An interrupted preview
retains the original key. Recovery returns the exact conversation and key for
authorized status lookup only. Missing or stale evidence quarantines recovery.
The journal carries neither prompt text nor provider credentials.

Travel owner erasure and trip deletion include the journal and binding. Ordinary
trip export keeps its existing domain document and excludes this operational
metadata. The preview-only journal does not establish a durable send fence
across Travel data deletion or restore. A subsequent dispatch proposal needs an
independently retained fence, canonical status and matching-detail checks, and
named native restore evidence before any continuation is wired.

## Reverse hop

A local, explicitly assembled FastAPI app may mount
`POST /internal/personal-ai/plan-source` with an independent
`PERSONAL_AI_PLAN_ENABLED` gate. Ordinary Travel composition does not mount it.
The isolated app pins one PAS target, registered client namespace, service
account and audience, and a separate Ed25519 delegation issuer and key. PAS
has to independently authenticate the human and authorize the exact conversation
before signing this request.

```json
{
  "schema_version": "travel-private-plan-source-v1",
  "purpose": "personal_ai_context",
  "destination_application_id": "travel",
  "workspace_id": null,
  "lane": "user_owned_plan",
  "target_id": "isolated-pas-preview",
  "client_namespace": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "conversation_id": "00000000-0000-0000-0000-000000000065",
  "operation": "current"
}
```

Travel verifies the service bearer against its pinned transport audience and
account through the isolated service verifier. It verifies a separate, fresh
`suite-delegation-v1` assertion in `X-User-Delegation` over exactly the JSON
object using the private delegation key and registered audience. The signed
request binds schema, purpose, target, lane, client, conversation and operation.
Travel rejects browser identity headers, extra properties, duplicate keys and
caller-selected refs. It rechecks the active identity, owner erasure marker,
exact private binding, and canonical trip ACL on each request.

The response uses the existing `travel-context-read-v2` schema and projector.
`current` returns one trip record within 4,096 bytes, with `trip_title` and
`trip_revision`. `verify` requires the decimal `expected_source_version` from
the prepared snapshot and projects only `trip_revision`. A changed revision
returns 409 without a projection. The original v2 source ID, decimal revision,
observation time, sensitivity, field classification, fingerprint and permission
ID/version remain in the result.

## PAS review handoff

Carry the authorized request-local conversation tuple into the source factory.
Use a bounded private planner path to admit the one domain source without
accepting trip selections in the public preview body. Receive the original
Travel projection into the existing assembler and preserve its provenance in
the prepared inventory. The PAS target registration and source permission need
a typed application-plan attestation distinct from the raw Travel revision.
Namespacing the revision by itself would be insufficient. Pair the projection
with separately authorized canonical PAS memory and exact PAS/runtime/model
recipient authority. Review revalidation at release, immediately before a
physical send, and on all outward content, replay and recovery paths. The
Travel route supplies read authority only.
