# Phase 26 — Personal AI context integration

Status: local review fixes implemented; P26.1 blocked; integration gates default off
Date: 2026-10-11

## Scope and ownership

Phase 26 adds the read-only Travel application context contract and a shared Personal AI sidecar on trip, day, and place views. Travel remains canonical for trip, itinerary, place, reservation, validation, authorization, persistence, and all final writes. The reviewed contract is typed HTTP (`travel-context-read-v2`); no Personal AI package imports or shared database were added.

Travel projects only requested fields and selected entity records after verifying the active Travel owner and selected trip. Each record carries opaque source identity, source/version, payload fingerprint, observed/freshness facts, sensitivity, and the current trip-revision permission dependency. Selected research-session references resolve directly through Personal AI's shared research evidence provider; Travel does not proxy Personal AI research. A canonical `research_source_policy` dependency is retained. Since no current policy revalidator is registered for it, selected research history fails closed on later reuse. Reservation summaries are minimized and sensitive. Derived reservation records carry a typed binding to the selected parent trip/day/item/place, which Personal AI validates against the original selection. There is no generic mutation operation.

The sidecar uses the shared Personal AI conversation lifecycle, planner, context provider runtime, policy, assembler, and preview. Shared profile/preferences, eligible memories, conversation history, current trip/entity state, and selected research are visible and can be narrowed. The pinned Travel model has no separate persisted travel-preference profile or trip-history store, so the UI does not imply those unavailable sources or add parallel ownership. Copy and draft insertion controls follow the shared Personal AI action policy. Travel persists an owner/trip anchor for each Personal AI conversation and checks it before history, preview, or continuation. The conversation-creation key is kept in session storage; a pending turn retains its idempotency key in component state for same-session retries. Prompt text and selected entity references are not added to browser storage. Conversation authority snapshots carry source dependencies into assistant history and summaries; stale or revoked source-derived turns are excluded before summary selection, count, embedding/memory extraction, and later generation.

## Contract and compatibility

- Travel API: `POST /v1/integrations/personal-ai/context`, schema `travel-context-read-v2`.
- Sidecar launch metadata: `application-launch-v1`; new conversations and preview use `application_id="travel"`, `workspace_id=null`.
- Independent gates default off: PAI `TRAVEL_CONTEXT_ENABLED`; Travel backend `PERSONAL_AI_CONTEXT_ENABLED`; frontend `NEXT_PUBLIC_PERSONAL_AI_CONTEXT_ENABLED`.
- Hosted transport keeps the Google user ID token distinct from the Cloud Run service ID token; both sides verify their respective identity/audience. The user token determines Travel ownership.
- No `trip_id` to `workspace_id` mapping is introduced.
- Travel's unsupported `destination` field and the `search` research-proxy operation are not part of v2. Selected research uses PAI's shared provider; Travel-sourced research records do not flow through this endpoint.
- The conversation anchor table is migration `0016`; it binds a Personal AI conversation ID to the verified Travel owner and trip ID.
- Existing direct `research-v1`, `booking-document-extraction-v1`, `itinerary-proposal-v1`, `travel-itinerary-context-v1`, `itinerary-proposal-policy-v2`, and travel-comparison contracts (`domain-lookup-v1`, `domain-comparison-v1`, `domain-module-v1`, `travel-comparison-v1`, `travel-features-v2`, `travel-sources-v1`, and the Travel response `travel-research-comparison-v1`) remain unchanged and separately gated.

Tested repository bases: Personal AI `78c7e4bc4678bd88a64c029c85f31e3bc66ef2c9`; Travel `c84433b9484133d9e054f8719e6e769e178af614`. The implementation was tested from the working trees based on those revisions; no commit was created. Source/test manifests: Personal AI 35 files, aggregate SHA-256 `fe494f66c0ba4462958ff07dd4aec9ff4ad247b9b1b173c3f41df89def6a4391`; Travel backend/frontend 19 files, aggregate SHA-256 `1afb6ddcac6b0a3a64de40418aa9e85e3bde73e752047cef14b678ad2558ba5b`.

## Verification

Recorded after implementation on 2026-10-11:

- Personal AI full backend suite after the final bounded revalidation changes: 1,365 passed, 82 skipped, 2 warnings.
- Personal AI Ruff: passed.
- Personal AI wheel and source distribution build: passed.
- Deterministic context, planner, memory, and memory-lifecycle evaluation runners: all exited successfully.
- Travel backend tests: **193 passed, 124 skipped, 1 warning**; Ruff and format check passed; mypy reported no issues in 102 files.
- Travel frontend Node tests: **59 passed**; ESLint, Next route type generation, `tsc --noEmit`, and production Next build passed.
- Travel markdown link check: **222 local links across 72 files**; both repository `git diff --check` runs passed.

The pnpm wrapper stopped at its non-TTY dependency cleanup guard; a CI-mode install then stopped at the ignored `unrs-resolver` build script. To keep that build-script policy unchanged, the same Node tests, ESLint, Next route type generation, TypeScript check, and production build entrypoints were run directly with the installed Node runtime and passed.

External checks not performed: deployed Cloud Run IAM invocation, live Google user/service tokens and audience alignment, live provider/data-use/retention approval, Travel database revision behavior under deployment, hosted provider behavior, and browser acceptance. Default-off gates are not evidence of live readiness.

## Independent post-implementation review follow-up — 2026-10-11

The attached review handoff was treated as defect-oriented input, with repository code and durable architecture remaining authoritative. The review fixes resolve M2 through M5 and S1 through S4 plus the shared fixture/evidence findings: v2 parent-selection binding for linked reservation summaries; direct canonical Personal AI selected-research lookup with preserved permission dependency; generic composition of context runtime contributions; UI use of the shared action policy; persistent owner/trip/conversation binding; feature-gated context capability and corrected disabled-planning order; removal of `destination`; and Travel-authoritative saved-place provenance. The cross-repository v2 fixture is byte-identical and tested in both repositories.

P26.1 remains **blocked and incomplete**. Automatic managed chat still injects Gemini directly and is not dispatched by P21. P21 workflow integration remains gated on Phase 15 membership and end-to-end revocation acceptance. The integration gates remain default-off; routed Travel turn attribution is not claimed. Selected Personal-AI-owned research history also remains non-reusable because its canonical policy dependency has no current revalidator.

Follow-up verification used Travel base `55e57e166f031971c166471cf0ff0bef9a91d1a3` and Personal AI base `51bc615bdf012d2d7a0339bc67bd257bb85c3d57`:

- Travel backend suite: **203 passed, 124 skipped, 1 warning**. The full suite passed with loopback permission for its existing local Google OIDC test server.
- Travel Ruff check and format check passed across 162 files.
- Frontend Node suite: **60 passed**; ESLint, Next route type generation, and `tsc --noEmit` passed.
- Targeted Travel context tests: **14 passed**; shared frontend action-policy test: **1 passed**.
- Personal AI full backend suite: **1,370 passed, 82 skipped**; full Ruff check passed. Its targeted cross-repository contract/runtime/research suite passed 63 tests.
- `git diff --check` passed in both repositories. Travel's link checker found **223 local links across 72 Markdown files**. PAI has no repository Markdown link-check command; changed documentation links were manually checked against their targets.

The tested source/test manifest fingerprints are Personal AI **16 files / `f879fb2788a19adbaabed1851aace5354a159803bb945f33abbbb58345f73d9c`** and Travel backend/frontend **15 files / `615f1889451f20fc94c00ba815489a63e96ea1bef89d79eec6e5362dc785a459`**. Each fingerprint is SHA-256 over sorted repository-relative source/test paths and file contents; documentation and ignored/generated files are excluded.

Cloud Run IAM, live identity tokens, provider/data-use approval, deployment, real database migration application, and browser acceptance were not performed. The skipped persistence/provider/cloud cases do not establish those environments.

## Review focus

Review source permission dependency versioning and revalidation order, transitive summary/history exclusion after revision change, hidden sensitive reservation field selection, Travel owner verification before repository disclosure, preservation of legacy external-turn authority digests, application-scope creation/idempotency, and the same-origin forwarding of only the user token. Confirm that no generic layer gained Travel-specific branches and that the direct Travel capability APIs retain their prior paths and idempotency.
