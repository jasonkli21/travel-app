# Phase 4 implementation plan — personal AI research

## Review corrections (2026-10-03)

The [Phase 0–4 audit](reviews/phase-0-4-audit.md) and ADR 0009 extend the
original acceptance criteria. Phase 4 itself still stores no AI evidence or
session data; review migration 0005 repairs pre-existing itinerary integrity.

- Bound create/run/detail by one elapsed deadline, default 45 seconds and
  maximum configurable 50, below the web proxy's 60-second deadline.
- Stream JSON before enforcing its 1 MB cap. Enforce SSE UTF-8 bytes before
  line allocation/decoding, including missing newline and CR/LF/CRLF cases;
  retain 16 KiB line/frame, 128 KiB stream and 128-event limits.
- Require a nonblank cited answer and safe credential-free HTTP(S) URLs.
  Present only unexpired completed output, with earliest citation/session expiry.
- Allow terminal-to-expired reconciliation only when expiry has elapsed.
  Nonterminal/replayed/inconsistent details fail closed without automatic retry.
- Project selected-day context in a worker thread and release SQL before
  external waits. Changes to that projected content invalidate browser output,
  including a response arriving after an edit.
- Guard duplicate submission and candidate writes; refresh failure after
  candidate creation cannot invite duplicate creation.
- Execute owner/day and atomic candidate tests in CI against migrated SQL,
  rather than relying on optional skipped database tests.

**Status:** implemented locally; independent review findings addressed

**Date:** 2026-10-03  
**Baseline:** `5720cc1` (`codex/phase-0-scaffold-corrections`), with Phases 1–3 delivered locally  
**Roadmap:** [`09-implementation-plan.md`](09-implementation-plan.md)  
**AI boundary:** [`06-ai-integration.md`](06-ai-integration.md), ADR 0003  
**Accepted downstream contract:** `research-v1` in `personal-ai-system`

## Goal

Add a manual-first, evidence-grounded research surface inside a trip workspace.
The travel application supplies a bounded projection of the selected trip day,
displays the returned cited result, and lets the user explicitly save a
manually entered place as a trip candidate. Research does not create or change
itinerary items, reservations, or places without an explicit user action.

The active `personal-ai-system` checkout contains the accepted Phase 5
`research-v1` API and its typed contracts. This phase consumes that existing
versioned HTTP API. It does not change `personal-ai-system`, share Python
packages, or add an endpoint there.

## Scope

### Deliver

- Extend the existing `PersonalAIClient` with typed `research-v1` session
  creation, bounded SSE run consumption, and final session-detail retrieval.
- Add a travel API request scoped to an owner-owned trip and one of its days.
  Validate the day belongs to the requested trip before contacting the AI
  service.
- Project only the trip date range/timezone, selected day date/title, and up to
  three short itinerary item/place labels and local times into the research
  question. Include the user's query. The final downstream question must stay
  within the AI contract's 500-character limit.
- Exclude reservation fields, confirmation codes, source references, itinerary
  notes, owner IDs, and trip/day UUIDs from the projection. Explain in the UI
  that the question and selected-day context go to the configured AI service
  and may be forwarded to its configured search provider.
- Add a backend `PERSONAL_AI_RESEARCH_ENABLED` gate, false by default. A
  disabled travel gate, disabled AI research endpoint, network failure,
  timeout, invalid response, or interrupted/malformed event stream produces a
  stable, safe `research_unavailable` error. Manual planning continues to work.
- Return only the AI session ID, terminal state, validated cited answer,
  failure code, expiry, and bounded citation metadata to the browser. Render
  answer text as text and allow only HTTP(S) source links.
- Add a responsive research panel to the trip workspace with selected-day,
  query, and freshness controls; clear pending, error, insufficient-evidence,
  and expired-result states; and visible citation/freshness details.
- Add a manual saved-place endpoint that atomically creates a user-entered
  place and its trip candidate relationship. The research panel never parses
  model prose into place fields; the user enters and confirms candidate data.
  The candidate can then be added to an itinerary through the existing item
  editor.
- Add focused backend client, context-projection, route, and candidate-service
  regressions. Keep the AI research panel behind safe error states when the AI
  system is not configured.

### Defer

- Iterative research, decision support, AI-generated structured place
  candidates, itinerary proposals, or model-driven mutations.
- Travel-side persistence or duplication of AI questions, evidence, citations,
  or research sessions. `personal-ai-system` remains authoritative for its
  research session and evidence lifecycle; the travel UI holds the latest
  result in memory.
- Direct browser-to-AI calls, shared Python imports, authentication, cloud
  deployment, booking/email imports, documents, background research, polling,
  and autonomous retries.
- A real Brave/Gemini search setup, provider credentials, retention changes,
  or provider-rights approval. The AI system's feature and provider gates stay
  under its own configuration.

## Accepted downstream contract

Use only the AI system's `research-v1` routes:

| Operation | AI system request/response |
| --- | --- |
| Create | `POST /v1/research` with `schema_version`, a 1–500 character `question`, `freshness` (`general` or `current`), and a UUID `idempotency_key`; returns a `ResearchSession`. |
| Run | `POST /v1/research/{session_id}/run`; consume only the bounded `research.*` SSE event names and validate schema version and session ID through a terminal event. |
| Detail | `GET /v1/research/{session_id}`; use the validated `answer`, terminal state, expiry, and citations. Do not forward internal queries, attempts, raw observations, or evidence records to the travel browser. |

The AI service requires `RESEARCH_ENABLED`; its real provider path also has
separate configuration and provider-rights gates. Both services remain
independently usable when research is disabled or unavailable.

## Travel API design

`POST /v1/trips/{trip_id}/research` accepts:

```json
{
  "day_id": "<UUID>",
  "question": "Find a quiet vegetarian dinner near the places on this day",
  "freshness": "current",
  "idempotency_key": "<UUID>"
}
```

The user query is bounded to 300 characters. The service validates trip/day
ownership, builds a deterministic context string under a 190-character budget,
and rejects any final question exceeding 500 characters. It includes no
reservation data or private notes. The API creates, runs, and reads the
downstream session in one request, consuming progress server-to-server and
returning the final bounded result. If the provider is disabled, unavailable,
or interrupted, it returns a safe error and does not mutate travel state.

`POST /v1/trips/{trip_id}/saved-places/manual` accepts a user-authored place
(`name`, optional `address`, `category`, `phone`, `website_url`, paired
coordinates, and `note`). The service creates the owner-scoped place and the
trip-scoped `saved_places` row in one transaction. It does not attach research
evidence to permanent travel state.

## Data and security decisions

- No migration is required. Research session/evidence storage remains in the
  AI system; saved candidate data uses the existing `places` and
  `saved_places` tables.
- The travel service sends no owner/trip IDs as research text and no
  reservation or note fields. It does not log user questions or upstream
  response bodies.
- Both the local travel gate and the AI system's research/provider gates
  default off. Enablement requires an operator to configure each service.
- The fixed `local` owner is still not authentication. This is a local-first
  integration only; no real private cloud data is in scope.
- Research output remains evidence, not a durable fact. The only persistent
  action in this phase is the user's explicit creation of a manual candidate.

## Work packages and commit boundaries

### P4.0 — Plan and contract check

Record this plan and confirm the accepted `research-v1` boundary before code
changes. Commit the plan first.

### P4.1 — Backend research boundary and manual candidate service

- Add typed AI request/session/citation DTOs and `PersonalAIClient` methods for
  create, bounded event-stream consumption, and detail retrieval.
- Add the opt-in setting, bounded active-day context projection, trip/day
  ownership checks, safe error mapping, and research response schema/route.
- Add atomic manual place-plus-saved-place creation using the existing tables.
- Add an ADR for the consumer-side context and evidence/state boundary, plus
  backend contract/client/service tests with mocked HTTP and repositories.

### P4.2 — Trip workspace research and candidate UX

- Add typed same-origin API calls and the responsive research/citation panel.
- Show that research may cross the configured AI/search boundary, expose
  freshness and failure/expiry states, and require the user to enter candidate
  fields before the manual save action.
- Refresh the existing trip workspace after candidate creation so the place
  can be added through the current itinerary editor.

### P4.3 — Documentation and release evidence

Update the product status, AI integration guide, data model notes, roadmap,
Codex handoff, and local configuration guide. Record commits, checks actually
run, and external credentials/provider limitations in a Phase 4 release note.

### P4.R — Independent review remediation

After implementation, ask a separate Luna Max agent to review this plan and
the complete implementation. Address valid plan, contract, security, and code
findings, then commit the review fixes together.

Do not create one commit per checklist item. Keep the plan, backend slice,
frontend slice, release documentation, and review remediation as coherent
commit groups.

## Acceptance criteria

- The client matches the accepted `research-v1` request/session/SSE contract;
  malformed, oversized, wrong-session, or nonterminal streams fail safely.
- A research request cannot reach the AI service when disabled or for an
  unknown/foreign trip/day. The composed question is deterministic and at most
  500 characters.
- No reservation confirmation data, references, notes, owner IDs, trip IDs,
  or day IDs leave the travel service as research context.
- The response/UI show the cited answer only when the AI session allows it,
  with each source URL, observation time, and expiry visible and safe to open.
  Stale/insufficient/failed results are clearly distinguished.
- Replayed AI sessions still in a nonterminal state fail safely. The UI clears
  results when the selected day, query, or freshness changes and hides results
  once their earliest session/citation expiry time passes.
- Provider/API failure leaves all travel records unchanged and does not break
  manual trip, reservation, itinerary, map, or saved-place workflows.
- Candidate saving creates both the manual place and trip relationship
  atomically, uses only user-entered fields, and leaves itinerary mutation to
  the existing explicit item editor.
- No evidence/session record or AI-derived place is silently copied into the
  travel database.
- Local setup documents both service URLs/gates and a fake-backed path; no
  credentials are required for mocked unit tests.
- No migration, direct browser-to-AI request, personal-AI Python import,
  authentication, cloud resource, or later-phase proposal feature is added.

## Verification plan

Add offline mocked tests for request packing/bounds and excluded fields, AI
client HTTP/event/session validation, upstream failure handling, trip/day
ownership, disabled behavior, and transactional manual candidate creation.
Review the frontend's accessible form/status behavior and citation-link
handling. Record only checks actually run; external Brave/Gemini, Firestore,
authentication, and deployment behavior remain unverified unless separately
configured.
