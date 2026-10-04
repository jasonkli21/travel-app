# Phase 5 groundwork independent review and remediation

Reviewed implementation: `76da3b2`, `9c99580`, `e3070b4`, `7b5b4d0`,
`a1cece6`, remediation `dd0974c`, and browser fixture `ea1576b`.
Date: 2026-10-03 (America/Los_Angeles).

## Review scope

The review covered migration/model parity, route and service write families,
revision accounting, locking, frontend request/recovery paths, bounded proposal
DTOs, opaque-handle correlation, immutable preview simulation, removal and
anchor rules, ordering/DST/conflict behavior, and release claims against the
Phase 5 plan and product invariants. Full Phase 5 remains gated on an accepted
upstream itinerary-proposal capability, absent at AI HEAD `0c397dc`.

## Findings and remediation

1. **High: browser revision headers were discarded by the proxy.** The typed
   client sent `X-Expected-Revision`, but `frontend/lib/proxy.mjs` forwarded only
   `Content-Type`, silently leaving every browser write in legacy last-writer
   mode. Commit `dd0974c` adds the explicitly allowlisted header and preserves
   omission. The regression calls typed item DELETE and shared-place PATCH
   methods through the proxy into a stable stale 409 response, then checks the
   API error and recovery result. It also checks that cookies, authorization,
   and arbitrary headers do not cross the proxy, and that an ordinary manual
   place POST still omits the precondition.
2. **High: explicit reload could re-enable stale mounted form drafts.** Commit
   `dd0974c` closes conditional editors and advances an editor generation only
   after every recovery read succeeds. The generation remounts item, reservation,
   place, day-title, saved-place-note, and research candidate forms against the
   refreshed snapshot. Ordinary post-save refreshes do not advance it. The
   recovery message explains that open editors will clear.

## Repeatable mounted-browser regression

This regression uses the checked-in fake API and does not connect to PostgreSQL,
AI, Geoapify, or another external service. From the repository root, start the
fake API in one terminal:

```bash
cd frontend
node tests/fixtures/revision-recovery-api.mjs
```

In a second terminal, start Next.js against it with the pinned pnpm version:

```bash
TRAVEL_API_URL=http://127.0.0.1:8011 \
  corepack pnpm dev
```

Open `http://127.0.0.1:3000/trips/trip-1`. Click **Edit** for “Old museum
title”, change the title, and save. The fake API returns a stale revision 409;
the workspace blocks edits and shows **Reload workspace**. Click it and verify
the editor closes. Reopen **Edit** and verify its title is “Remote museum
change”. Then change the open form title to “Draft after quick-place update”,
enter “Quick Cafe” in **Quick-create place**, and click **Add place**. Verify
the item title draft remains and the Place selector now contains “Quick Cafe”.
Restarting the fake API resets the fixture to its initial revision-4 state.

This exact sequence was run in Chrome on 2026-10-03 against a loopback Next dev
server and the checked-in fixture.

## Verification and limits

- Pinned pnpm 10.17.1 frozen offline install passed, reusing 340 packages. The
  existing package script approval policy was preserved; `unrs-resolver` was
  reported as an ignored build script.
- Frontend Node suite: **18 passed, 0 failed**. The revision request test also
  passed directly under Node 22.23.3. ESLint, route type generation, strict
  TypeScript, and the production build passed.
- The mounted Chrome scenario above passed. It verified editor close/reset after
  stale recovery and draft preservation during quick-place creation.
- The remediation changed frontend code only. The prior migrated PostgreSQL
  backend result remains **110 passed, 0 skipped**; it was not rerun. Ruff and
  strict mypy from that unchanged backend checkpoint passed. No live upstream
  API, AI, Geoapify, hosted CI, or cloud service was exercised.

Phase 5 remains incomplete. Do not start P5.3 generation or lifecycle work until
`personal-ai-system` accepts a versioned itinerary-proposal contract and
capability gate. Proposal storage, apply/replay, rejection, generation, and UI
remain undelivered.

## Coordinator re-review

The coordinator re-reviewed the remediation through `5167115` and found the
explicit proxy allowlist and successful-reload editor reset resolve both
findings. Independently rerun: 110 migrated PostgreSQL tests with zero skips,
Ruff check/format, strict mypy, 18 frontend tests, ESLint, TypeScript/typegen,
and production build; all passed. The backend emitted the existing
Starlette/httpx deprecation warning. No further feature or upstream work was
performed. The missing accepted upstream contract is the next required input,
not a completed integration.
