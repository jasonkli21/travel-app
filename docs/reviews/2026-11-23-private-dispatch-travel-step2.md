# Travel private dispatch Step 2 evidence

The owner accepted the shared Step 1 decision and the Travel appendix in the
handoff conversation. This candidate implements Travel against
`pas-dispatch/1` from the reviewed archive. The base is
`9f95c3de42a64b8817a5d76afa1782b1f3b05777`, tree
`43d97dcb4c7aa0b4ae2b8466f4da69b55893ba88`.

| Accepted material | SHA-256 |
| --- | --- |
| Document 02, joint decision and ABI | `3d61f1c66cc9337dd470d8a6b7bf3a921cec7e5cea7d88e4f04856249496e89d` |
| Document 06, Travel authority appendix | `bfca4f2c54a43965834e39c0627e5c26a7d407f15368446ac7fd7b6b548d5147` |
| Travel golden binding | `55dad190dd42f782ea9a4ad1808d47c31f7498f13a9fca459e7b5e247e8b6e1d` |
| Travel golden confirmation | `0c4697da808ba4b9c20a5642321596d64a895c3d875b26419cf7fbe26c0aadea` |
| Health golden binding | `c073c97a49db8469f8e6d116447235a538bd5d28f2a66c479bf99c90f2f6f998` |
| Health golden confirmation | `543ecdf4f07785ce07d38efd3590a3ef45e163677112e10463e2f18d282e473b` |

The [golden files](../../backend/tests/fixtures/pas_dispatch_v1) preserve
both transcripts as raw UTF-8 byte strings. The Travel and Health preview
claims hash to `cb46aa61944b27228d48af781b913acde02e28b92bea4b97cb117f845e0a0edb`
and `425d8fb8c2e9f14cf3106fa426b2b56f182e0a218dc17024bc8803b61dbb657c`.
The received fake payload commitments in the transcript are
`3598d0ff38a7de11a47e352399a0fde5b7c49b6fb95ae3b5ef248ee5b1761936` and
`ad8c991290d66ec7ea08e81ec90bf1d2844fc672f108ed993fc853536cc0f97e`.

## Travel boundary

The local private composition requires both the existing explicit settings
gate and a synthetic registration. It checks a live native Travel session,
issuer/subject, owner, original private conversation anchor, selected trip, and
trip revision. The registration independently supplies the opaque PAS owner,
client, tenant, installation, role-pinned keys, current grants, independently
held binding allocation, and continuity reader. Current checks recompute the
keyed commitment to the exact `travel-context-read-v2` private trip
projection, field order, policy, permission epoch, and distinct source,
model, and runtime grants. The private source adapter consumes a fresh nonce
for each phase under its registered in-process capability.

The negotiated preview carries the original signed metadata and
`x-application-plan-receipt`, with the additional canonical ASCII
`{claims,signature}` envelope in `x-pas-dispatch-receipt`. The vendor
media type selects the synthetic profile, including its displayed fake
destination. The confirmation body must be the exact current signed
confirmation selected by the user. Travel verifies the original artifacts
under their legacy signature rules and uses the detached ASCII receipt
header digests in the new binding.

Migration 0023 adds an immutable, owner-scoped original-key confirmation
row containing exact content-free confirmation bytes, digests, attempt,
root and submission time. The transaction locks the identity, session, binding,
anchor, trip, and preview intent before inserting the marker. A second
connection observes the committed row before the first confirmation HTTP
request. The winning caller rechecks current authority and the prepared
signature before posting once. Followers and restarted callers use the
original-key GET. A postcommit denial consumes the send opportunity.

Status and detail are reauthorized around each request and before disclosure.
Travel checks terminal signature and attempt/body/binding before selecting the
original-key detail and compares the exact UTF-8 result digest before returning
text. It quarantines contradictory evidence and stale authorized reads.
The journal stores no preview, result, trip text, bearer, or PAS memory.
It participates in owner erasure rather than exporting confirmation bytes.

## Matrix observations

These are implementation observations for independent review. HTTP counts
come from an in-process fake PAS `httpx.MockTransport` handler. SQL
transactions use disposable PostgreSQL, including separate worker
connections and a database restored from a native dump.

| ID | Observation |
| --- | --- |
| T01 | Signed synthetic preview leaves zero confirmation rows and zero confirmation POSTs. Missing human confirmation returns 409. |
| T02 | The fake handler opens a separate SQL connection and observes the already-committed timestamp, body, and digest before accepting its only confirmation POST. |
| T03 | Concurrent workers on separate SQL sessions issue one confirmation POST and one original-key GET. Changed body conflicts, and crossed authority is denied before transport. |
| T04 | Direct session revocation, expired confirmation, owner freeze, and current revision changes deny the requested operation. The active private anchor and current identity are checked from native rows. |
| T05 | Another trip under the same owner and a crossed signed receipt cannot authorize the first trip. The prior native tests also exercise distinct owners with the same email. |
| T06 | Transport tests compare canonical byte buffers, separate PAS bearer, exact media and request paths, bounded responses, and a single call on a redirect. Synthetic network composition requires loopback and verified TLS. |
| T07 | Timeout, cancellation, 429, partial SSE, and partial status each leave one confirmation POST. Fresh callers recover only by GET. A failed postcommit continuity check leaves the marker with zero POSTs. |
| T08 | Matching signed terminal and detail publish synthetic result text. Wrong signed root and a wrong result digest suppress output and quarantine the attempt. |
| T09 | Revision or grant changes before status, after status, and after detail suppress output. The authorized in-flight cases quarantine the journal row. |
| T10 | 0023 applies above the original quarantine, and Alembic reports no schema drift. SQL rejects marker modification and downgrade with retained submissions. Restoring a native pre-submission database dump separately from a retained fixture checkpoint prevents a second POST. |
| T11 | Existing private sources, automatic sidecar, Shopping, proposals, and imports are covered by repository checks. |
| T12 | The ordinary app has no dispatch mount. The workflow depends on an explicit trusted registration and a synthetic destination. Normal frontend checks use the existing application. |

The independently retained checkpoint used in T10 is a fixture outside the
restored database. Durable cross-system checkpoint custody and an approved
retention and erasure protocol are pending. A v1 status without authenticated
commit time cannot support archived terminal disclosure after signed expiry.
Paired PAS execution, independent review, and production authorization are
outside this Step 2 candidate. The source checks cannot serialize separate
revocation writers with an outward socket write.

## Repository checks

- `make backend-test` used the locked Python 3.12.14 environment and a
  dedicated PostgreSQL 16.2 database at loopback port 55443. The suite ran
  378 tests with 376 passes and two failures in one complete run. The
  synthetic Uvicorn startup and PDF subprocess deadlines also failed on
  the unchanged base under contemporaneous load. Both dispatch fixture
  groups, the private source and journal regression groups, and the native
  migration and restore tests passed.
- `make backend-lint backend-typecheck docs-check` passed. The historical
  migration files 0021 and 0022 have no diff from the base.
- `make frontend-install frontend-check` used the frozen pnpm lock. Lint,
  type checking, and all 68 frontend tests passed.
