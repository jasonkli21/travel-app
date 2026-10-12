# Phase 30 Travel federation export

Date: 2026-10-11. Tested baseline: `cb838ff` plus the local Phase 30 changes.

Travel owns a per-trip destination and `cross_domain_sharing_allowed` flag. Consent defaults off. Updating either field advances the trip revision. The existing default-off Personal AI context endpoint now supports the exact `shopping_trip_context` purpose for Shopping, exporting only destination, start date, and end date for one selected trip. A verify request returns the current trip revision and permission dependency. Unconsented reads fail closed, and a revoked or changed trip no longer matches the grant's stored dependency. Destination may be explicitly empty; the source export remains complete and carries that null field rather than broadening the request.

The Personal AI side registers a generic Travel source adapter behind `travel_federation_enabled`; it validates the source policy, exact purpose/fields/entity, completeness, and dependency before returning the broker result. No Health-specific destination path was added to Travel.

Checks on the working tree: Personal AI context source tests — **20 passed**; Ruff passed. Frontend trip-workspace draft/workflow regression tests — **3 passed**; TypeScript and ESLint checks on changed frontend files passed. Alembic revision `0017_trip_federation_export` follows `0016` and is the expected head.

The source export is not end-to-end product acceptance. Personal AI and Travel identity/deployment checks remain default-off and unverified; Shopping has no user-bound typed task host, grant selection surface, or shared-sidecar host. Health → Travel remains unavailable because the shared broker cannot provide Health's required delegated Firebase token. No live grant/revocation or browser checks were run.
