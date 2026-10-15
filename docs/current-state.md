# Current implementation state — Travel

Updated 2026-10-14. This is the living delivery boundary. Code, contracts,
migrations and tests take precedence over plans. The stabilization patch is based
on `09584976d46c67c97a19c72d173b2ee88dbcb31c`; final commit/source identity and exact checks are recorded in the
[verification record](stabilization-2026-10-14.md). Independent final review and
external acceptance remain open.

## Implemented locally

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

The task panel now selects an active Shopping project through the read-only
Personal AI target-choice route and persists a distinct create, submit,
confirm, and effect-uncertain phase. Recovery retries the original create or
submission with the same command ID, and apply uncertainty reconciles the
original execution. Recoverable state is scoped to the authenticated owner and
cleared on owner change. Browser rendering and native session behavior still
need independent acceptance.

## Verification and release boundary

Read [the local stabilization evidence](stabilization-2026-10-14.md) and
[suite verification](https://github.com/jasonkli21/personal-ai-agent/blob/main/stabilization/VERIFICATION.md).
The all-five synthetic PostgreSQL/loopback HTTPS paths do not prove real IAM,
provider, cloud, browser or native-device behavior. Feature gates remain off by
default. No cloud deployment or live-provider operation was run. The user
authorized local commits followed by pushes to each remote `main`. Hosted CI is
separate evidence.

## Minimum context

Read [the router](README.md), the owning module/contract and focused tests.
[Suite contracts](https://github.com/jasonkli21/personal-ai-agent/blob/main/stabilization/CROSS-REPO-CONTRACTS.md),
[operations](https://github.com/jasonkli21/personal-ai-agent/blob/main/stabilization/OPERATIONS.md) and the
[fresh re-review handoff](https://github.com/jasonkli21/personal-ai-agent/blob/main/stabilization/FRESH-REVIEW-HANDOFF.md) explain
cross-app assumptions and remaining acceptance. The
[previous status](history/stabilization/current-state-before-2026-10-14.md)
preserves detailed predecessor phase evidence and unresolved gates. This status
does not authorize implementation of the future roadmap.
