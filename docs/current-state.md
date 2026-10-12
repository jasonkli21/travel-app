# Current state

Updated: 2026-10-11

This is the single living status snapshot. Read the linked release and review
records for detailed scope, checks, and limitations; do not infer delivery from
a plan.

## Accepted locally

- Phases 0–6 are implemented and locally reviewed. Phase 6 includes verified
  identity, private-source intake and parsing, the accepted booking-document
  extraction contract, durable candidate review/recovery, and explicit
  reservation confirmation. Private input and AI capability switches remain
  default-off where external gates apply.
- Evidence: [Phase 0–4 audit](reviews/phase-0-4-audit.md), [Phase 5 release](releases/phase-5-local-proposals.md) and [lifecycle review](reviews/phase-5-lifecycle-review.md), [Phase 6 release](releases/phase-6-booking-imports.md) and [whole-phase review](reviews/phase-6-whole-review.md).
- The post-review Phase 2 frontend workspace cleanup is implemented and
  locally validated without changing travel capabilities or API contracts.
  See the [workspace cleanup evidence](releases/post-review-phase-2-workspace-cleanup.md).

## Implemented, exit gates open

- **Phase 7:** initial default-off source comparison for food, activities,
  neighborhoods, and day trips. Whole-phase evidence/provider gates remain
  open; dates, prices, hours, availability, accessibility, travel duration,
  and preference retrieval are unsupported. See the [release](releases/phase-7-travel-comparison.md), [review](reviews/phase-7-independent-review.md), and [plan](phase-7-implementation-plan.md).
- **Phase 8:** local private trip and reservation attachments, revision-stamped
  HTML/ICS/JSON exports, and a read-focused travel view. Attachments and
  booking sources use `LocalSourceStore`; no shared cloud object store is
  implemented. Private input requires verified Google identity. Hosted
  configuration rejects private imports or attachments while storage is
  local-only. Whole-phase verification remains open. See the
  [release](releases/phase-8-attachments-exports.md), [ADR 0015](decisions/0015-phase8-attachments-and-exports.md), and [plan](phase-8-implementation-plan.md).
- **Phase 9:** a local operational-hardening slice for provider quotas, hosted
  identity configuration, backup/restore tooling, containers, and CI is
  implemented and locally validated; **Phase 9 is not accepted**. CI checks
  Python dependency advisories, tracked secrets, synthetic encrypted SQL/blob
  recovery invariants, and bounded pool failure/recovery. A local large-trip
  benchmark is available for operator evidence. The owner-wide deletion
  workflow and restore-time deletion fence remain unimplemented engineering
  gates; approved deletion disposition, full privacy/browser acceptance, image
  scanning/digest pinning, real recovery objectives, hosted behavior, and
  deployment gates also remain open. See the
  [readiness inventory](releases/phase-9-operational-hardening.md#post-review-phase-3-readiness-inventory), [ADR 0016](decisions/0016-phase9-operational-hardening.md), [plan](phase-9-implementation-plan.md), and [operations runbook](runbooks/phase-9-operations.md).
  The October 8 post-implementation fixes for hosted-mode binding, CI fixture
  isolation, stale item/place drafts, interrupted backup states, and readiness
  classification are recorded in the [review follow-up](releases/phase-9-operational-hardening.md#post-implementation-review-fixes-2026-10-08).

## Local and hosted capability matrix

| Capability | Local development | Hosted / cloud |
| --- | --- | --- |
| Canonical trips, itinerary, reservations, and places | Implemented. Local-owner mode is the development default; verified Google OIDC is also implemented. | Hosted identity and configuration guards are implemented, but no deployment or production behavior has been provisioned or verified. |
| Research, proposal generation/apply, and travel comparison | Typed clients and Travel-side validation are implemented. Travel and upstream feature/provider gates default off. Current direct capability calls use standalone Personal AI scope. | Hosted service identity, provider behavior, and deployment are unverified. Application-scope migration is not implemented. |
| Personal AI context sidecar | Implemented as an optional read-only trip/day/place assistant using typed Travel context reads and shared Personal AI conversation/preview APIs. Travel, Personal AI backend, and frontend gates default off. See the [Phase 26 release](releases/phase-26-personal-ai-context.md). | Cloud Run service IAM, live user assertions, provider/data-use approval, deployed revision checks, and browser acceptance are unverified. |
| Booking-source import and extraction | Implemented behind default-off gates with verified Google identity and local private storage. Local-auth mode cannot access private input. | Startup rejects private imports because only local filesystem storage exists. |
| Trip and reservation attachments | Implemented behind a default-off gate with verified Google identity and local private storage. Local-auth mode cannot access private attachments. | Startup rejects private attachments because only local filesystem storage exists. |
| Private blob storage | `LocalSourceStore` stores opaque objects on a separately configured local filesystem path. | No durable shared object store is implemented. GCS is not implemented or authorized. |

## External gates and boundaries

- Google OAuth, Cloud Run IAM, hosted deployment, and real private-input
  verification are not provisioned or verified.
- GCS is not implemented or authorized. Private imports and attachments are
  default-off, require verified Google identity even in local deployment, are
  unavailable in local-auth mode, and are rejected at hosted startup until a
  supported durable shared object store exists.
- Live AI, Geoapify, and provider approval/quality checks are separate from
  local implementation evidence. No live provider result establishes durable
  travel state.
- Existing database upgrades that require legacy data repair need online
  inspection and a backup before migration.

## Personal AI boundary

Phase 26's reviewed read boundary uses `travel-context-read-v2` at `POST /v1/integrations/personal-ai/context`. It returns requested bounded trip/day/itinerary/place fields; selected Personal-AI-owned research goes through the shared Personal AI evidence provider rather than a Travel proxy. Reservation summaries are minimized, sensitive, and carry typed parent bindings when derived from a selected trip/day/item/place; Personal AI checks each binding against the original selection. Every Travel record carries a trip revision, source fingerprint, and owner-scoped permission dependency; Personal AI rechecks Travel authority before derived history is reused. Canonical research history fails closed on later reuse until its current policy revalidator is implemented. The sidecar fetches the shared Copy/draft action policy, and a persisted Travel owner/trip/conversation mapping prevents cross-trip reuse. There is no generic itinerary or booking mutation operation. P26.1 remains blocked because automatic chat still uses direct Gemini instead of P21 routing; Phase 26 is incomplete and its integration gates remain off. `application_id="travel"` and `workspace_id=null` remain in force, and no trip-to-workspace mapping exists. Existing standalone capability contracts remain unchanged.

Travel owns canonical trips, itinerary ordering/state, bookings and
reservations, travel validation, authorization, persistence, and final
mutation/application. Personal AI owns reusable model/provider access,
evidence/context machinery, memory/retrieval infrastructure, shared
research/decision capabilities, and reusable orchestration. It returns
evidence, recommendations, or typed proposals; Travel rechecks authorization
and current state, validates, previews, and performs canonical writes.

Personal AI's registry contains a Travel application definition. That metadata
does not change the current direct endpoint contract: Travel's research,
booking-extraction, proposal, and comparison calls use standalone Personal AI
application scope and omit `X-Application-ID: travel`. Migrate only after
Personal AI exposes the relevant capabilities through its supported
application-integration contract. No workspace identity or trip/workspace
mapping is defined.

## Current work boundary

Phase 26's local review fixes are implemented behind default-off gates. P26.1 remains blocked pending Phase 15 authorization/revocation acceptance and shared P21 workflow dispatch; local checks do not establish live cloud identity, provider approval, deployment, or browser acceptance. See the [Phase 26 release follow-up](releases/phase-26-personal-ai-context.md) and [integration contract](06-ai-integration.md).

Phase 9 remains the latest unfinished product phase. Phase plans are historical
implementation intent and do not authorize new work. Any further feature or
operational work needs an explicit current request and must follow its plan and
open gates. Documentation alone never authorizes cloud deployment.

Phase 30's Travel → Shopping source export is implemented locally behind
`PERSONAL_AI_CONTEXT_ENABLED=false`. A trip has a revisioned destination and
owner-controlled sharing flag; the source endpoint exports only destination,
start date, and end date for the exact Shopping purpose, and rechecks the same
dependency on verification. The shared Personal AI adapter consumes that
contract through the generic broker. The authenticated Travel choice-list
route is classified as a Personal AI sidecar operation; its browser contract
forwards opaque choice IDs and displays only safe labels. Personal AI resolves
the IDs to current canonical grants and repeats full source authorization at
actual context use. The complete product flow remains gated: there is no
accepted Shopping user-bound task host, and live identity, grant selection,
deployment, and cross-app revocation acceptance remain open. See [Phase 30
Travel export evidence](releases/phase-30-federation-export.md).
