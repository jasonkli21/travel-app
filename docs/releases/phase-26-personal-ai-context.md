# Phase 26 — Personal AI context integration

Status: locally implemented; independent integration gates default off; external acceptance remains open  
Date: 2026-10-11

## Scope and ownership

Phase 26 adds the read-only Travel application context contract and a shared Personal AI sidecar on trip, day, and place views. Travel remains canonical for trip, itinerary, place, reservation, validation, authorization, persistence, and all final writes. The contract is typed HTTP (`travel-context-read-v1`); no Personal AI package imports or shared database were added.

Travel projects only requested fields and selected entity records after verifying the active Travel owner and selected trip. Each record carries opaque source identity, source/version, payload fingerprint, observed/freshness facts, sensitivity, and the current trip-revision permission dependency. Research is returned only from selected completed evidence sessions with valid unexpired citations. Reservation summaries are explicit, minimized, and sensitive. There is no generic mutation operation.

The sidecar uses the shared Personal AI conversation lifecycle, planner, context provider runtime, policy, assembler, and preview. Shared profile/preferences, eligible memories, conversation history, current trip/entity state, and selected research are visible and can be narrowed. The pinned Travel model has no separate persisted travel-preference profile or trip-history store, so the UI does not imply those unavailable sources or add parallel ownership. The user can Copy or insert a non-authoritative draft. The conversation-creation key is kept in session storage; a pending turn retains its idempotency key in component state for same-session retries. Prompt text and selected entity references are not added to browser storage. Conversation authority snapshots carry source dependencies into assistant history and summaries; stale or revoked source-derived turns are excluded before summary selection, count, embedding/memory extraction, and later generation.

## Contract and compatibility

- Travel API: `POST /v1/integrations/personal-ai/context`, schema `travel-context-read-v1`.
- Sidecar launch metadata: `application-launch-v1`; new conversations and preview use `application_id="travel"`, `workspace_id=null`.
- Independent gates default off: PAI `TRAVEL_CONTEXT_ENABLED`; Travel backend `PERSONAL_AI_CONTEXT_ENABLED`; frontend `NEXT_PUBLIC_PERSONAL_AI_CONTEXT_ENABLED`.
- Hosted transport keeps the Google user ID token distinct from the Cloud Run service ID token; both sides verify their respective identity/audience. The user token determines Travel ownership.
- No `trip_id` to `workspace_id` mapping is introduced.
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

## Review focus

Review source permission dependency versioning and revalidation order, transitive summary/history exclusion after revision change, hidden sensitive reservation field selection, Travel owner verification before repository disclosure, preservation of legacy external-turn authority digests, application-scope creation/idempotency, and the same-origin forwarding of only the user token. Confirm that no generic layer gained Travel-specific branches and that the direct Travel capability APIs retain their prior paths and idempotency.
