# Phase 4 release — bounded AI research

**Status:** implemented locally; independent review findings addressed

**Date:** 2026-10-03  
**Plan:** [`phase-4-implementation-plan.md`](../phase-4-implementation-plan.md)  
**Decision:** [`0008-personal-ai-research-context.md`](../decisions/0008-personal-ai-research-context.md)

## Delivered

- A typed `PersonalAIClient` for `research-v1` session creation, bounded SSE
  consumption, and durable detail retrieval.
- A travel API route that validates the owner-scoped trip/day and projects no
  more than 190 characters of date, timezone, day title, and selected item/place
  labels and local times into a 500-character downstream question.
- A disabled-by-default travel research gate, safe upstream errors, citation
  URL/session validation, and no research-session or evidence persistence in
  the travel database.
- A trip-workspace form with a transfer disclosure, freshness selector,
  cited plain-text results, source observation/expiry times, and explicit
  user-entered candidate fields.
- An atomic manual place plus trip candidate endpoint using existing SQL
  tables. The existing item editor remains the explicit way to add it to a day.
- Focused mocked client/context tests and PostgreSQL-backed candidate tests.
- ADR 0008 and updated product, architecture, data, AI integration, setup,
  roadmap, and handoff documentation.

No database migration or frontend dependency was added. `personal-ai-system`
was not changed.

## Commit slices

1. `37949cf` — `docs: plan Phase 4 AI research integration`
2. `3d9f117` — `feat: add bounded personal AI research API`
3. `9f53c48` — `feat: add trip-day AI research panel`
4. Phase 4 documentation and release record
5. Independent-review remediation

## Review and verification

- An independent Luna Max reviewed the plan and implementation and identified
  four issues: nonterminal idempotent replays could look successful, results
  could outlive edited inputs or their expiry, citations did not display each
  raw source URL, and route-level ownership/upstream-error regressions were
  missing. The remediation makes nonterminal replays fail safely, clears stale
  results on input edits, expires visible results at the earliest evidence or
  session expiry, displays source URLs, and adds route regressions proving
  trip/day ownership checks run before the AI call and upstream bodies remain
  hidden.
- All independent-review findings are addressed in the plan and implementation.
- Automated tests, type checks, lint, and builds were not run in this session.
  The focused test cases, including the added route regressions, are committed
  as source but remain unexecuted.
- No live AI service, external search provider, provider credentials, or
  PostgreSQL-backed Phase 4 test database was configured for this work.
- The local travel research gate defaults to off. The AI service's research
  and provider gates are separate. Fake-backed AI output is synthetic and is
  not evidence of live provider behavior.
- A local owner ID is not authentication; private cloud data, booking imports,
  and AI proposals remain out of scope.
