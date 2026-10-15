# Travel stabilization evidence — 2026-10-14

Base revision: `09584976d46c67c97a19c72d173b2ee88dbcb31c`. This record covers the committed stabilization patch,
including earlier Luna changes reconciled by direct implementation QA. Independent
final review is intentionally owned by a separate session.

Travel owns trips, itinerary, reservations, places, private attachments, imports,
review, exports, revisions and UI. The existing source-comparison/proposal gates
remain bounded. Hosted private imports remain closed until shared private object
storage and deployed identity are accepted; LocalSourceStore is not a cloud store.

The default-off Shopping note path sends only consented destination/dates from
the canonical trip, through shared source command/confirmation/status transport.
Trip titles and richer private itinerary data are excluded. Migrations0018/0019
retain actual authentication time and immutable owner-scoped commands.

Migration0020 adds an independently retained erasure fence. Canonical writes
serialize with owner freeze. Bounded cleanup removes owned SQL and private bytes;
upstream extraction deletion stays pending until observed. Current ledger replay
is required before isolated encrypted restore promotion. Historical completion
flags cannot certify restored rows, and retained markers block downgrade.
This operator foundation is not a hosted account-erasure product workflow.

Recovery's schema check now derives the current head. The encrypted smoke at0020
verifies SQL/byte hashes, pending-upload recovery, export, owner isolation and a
deletion requested after backup. Baseline web/native checks pass locally; native
browser, cloud recovery/retention and approved RPO/RTO remain open.

The offline full secret scan covers358 source files. The new generated wire
contains a public base64url alphabet; that exact constant was reviewed as a
false positive and added to the baseline. Existing synthetic entries and their
shifted line locations are preserved. No key or token was added to the allowlist.

Fresh Ubuntu CI exposed a fixed-length tuple inference in the non-Darwin
parser limit branches. Explicit variable-length tuple annotations preserve all
resource limits; mypy is checked for Linux as well as the local platform.
Cross-repo documentation links use GitHub so a standalone checkout resolves
its local documentation without requiring sibling repositories.

The final password-authenticated CI run exposed a pool test converting a
SQLAlchemy URL to its masked display string. It now passes the URL object
directly, preserving the actual synthetic password. Native pool/recovery behavior
remains asserted; local trust-auth success did not establish this boundary.

## Evidence authority

[Exact commands/counts/source identity](https://github.com/jasonkli21/personal-ai-agent/blob/main/stabilization/VERIFICATION.md),
[fresh re-review handoff](https://github.com/jasonkli21/personal-ai-agent/blob/main/stabilization/FRESH-REVIEW-HANDOFF.md)
is the suite evidence index. Earlier records retain their original date/results;
newer local evidence supersedes their unrun-local-check statements only.

No personal records were used or deleted. Local tests created disposable databases
and synthetic private-store directories. Root-owned test processes are stopped at
handoff. Credentials, private payloads and unrelated user files are excluded from
commits. Cloud/provider/device/hosted CI acceptance cannot be inferred from this
local patch, and erasure does not independently invalidate previously downloaded
exports or historical cloud backups.
