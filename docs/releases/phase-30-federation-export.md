# Phase 30 Travel federation export

Date: 2026-10-11. Tested baseline: `cb838ff` plus the local Phase 30 changes.

Travel owns a per-trip destination and `cross_domain_sharing_allowed` flag. Consent defaults off. Updating either field advances the trip revision. The existing default-off Personal AI context endpoint now supports the exact `shopping_trip_context` purpose for Shopping, exporting only destination, start date, and end date for one selected trip. A verify request returns the current trip revision and permission dependency. Unconsented reads fail closed, and a revoked or changed trip no longer matches the grant's stored dependency. Destination may be explicitly empty; the source export remains complete and carries that null field rather than broadening the request.

The Personal AI side registers a generic Travel source adapter behind `travel_federation_enabled`; it validates the source policy, exact purpose/fields/entity, completeness, and dependency before returning the broker result. No Health-specific destination path was added to Travel.

Checks on the working tree: Personal AI context source tests — **20 passed**; Ruff passed. Frontend trip-workspace draft/workflow regression tests — **3 passed**; TypeScript and ESLint checks on changed frontend files passed. Alembic revision `0017_trip_federation_export` follows `0016` and is the expected head.

The source export is not end-to-end product acceptance. Personal AI and Travel identity/deployment checks remain default-off and unverified; Shopping has no user-bound typed task host, grant selection surface, or shared-sidecar host. Health → Travel remains unavailable because the shared broker cannot provide Health's required delegated Firebase token. No live grant/revocation or browser checks were run.

## 2026-10-12 third-review remediation

The hosted `GET /v1/trips/{trip_id}/assistant/federation-choices` path is now
included in Travel's central authenticated sidecar classifier. It receives the
same server-built Personal AI auth context and preserves session/user identity
checks. The route and Personal AI client contract now exchange only opaque
43-character choice IDs and safe category labels; no source entity, grant ID,
permission revision, or field list is sent to the browser. Travel forwards
selected IDs only through the shared preview/continuation flow.

Tested fix commit: `bb275d6f9b1e3ad041ec22367c4ca039fcb3945d` (parent
`0af49a9e72d65065808a5f2ea4bc21736339f214`). The focused backend context/client
suite passed **57 tests**; changed-file Ruff, frontend ESLint, and TypeScript
`--noEmit` passed; frontend Node tests passed **60 tests**. The new hosted
middleware integration test is present but skipped because `TEST_DATABASE_URL`
is unset. No live Google/Cloud Run identity, Personal AI origin, database, or
browser acceptance is claimed. The complete Health → Travel flow and all
Shopping-bound flows remain open pending the broader Personal AI/Health
remediation and the dedicated Shopping destination-host subphase.
