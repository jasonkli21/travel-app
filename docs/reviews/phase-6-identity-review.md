# Phase 6 identity independent review

Baseline: `9a80ff1`; coordinator independently ran 170 backend tests against
migrated disposable PostgreSQL, zero skips. Identity stage remains open.

## Required remediation

1. **UI writes omit CSRF (high).** `api-request.mjs` never reads the session
   CSRF cookie or supplies `X-CSRF-Token`; the proxy only forwards an existing
   header. Normal trip/item/place/reservation/proposal writes will receive 403
   after Google sign-in. Add the proof in the centralized client for unsafe
   methods, with duplicate/malformed cookie handling and local-mode compatibility.
   Verify real typed calls and mounted authenticated CRUD, not only proxy mocks.
2. **Migration leaves invalid nested owners (high).** Owner migration changes
   only `base_snapshot.owner_id`; real proposal item/place/candidate/reservation
   snapshots retain the old owner. `validate_proposal_snapshot` rejects these,
   making a migrated ready proposal unusable without any revision change.
   Validate and deliberately migrate the typed complete snapshot; reject any
   foreign nested owner. Test a real ready proposal through apply after transfer.
   Settle generating/unknown upstream work explicitly: its old-owner key/result
   cannot safely be reconciled as the new owner. Never silently re-dispatch it.
3. **Service token `azp` check (high).** The code requires optional service-token
   `azp == audience`, rejecting Google's normal numeric service identity. The
   [Google token reference](https://docs.cloud.google.com/docs/authentication/token-types)
   documents service-account `azp`/`sub` as the requesting service account unique
   ID and `aud` as the recipient. Validate the actual service token contract,
   preserve signature/audience/account/time checks, and use realistically signed
   claims in tests (rather than omitting `azp`).
4. **Bounded auth work and event-loop responsiveness (high).** OAuth exchange
   has only per-I/O five-second timeouts; signing-key transport likewise uses
   per-I/O three-second timeouts. Trickling responses can consume many minutes
   under the byte caps. Middleware performs synchronous session SQL and Google
   verification directly in an async ASGI method; async callback also performs
   sync SQL/verification on the event loop. Offload concrete sync work, enforce
   one elapsed auth budget through lock/key-fetch/count/response processing,
   clean up timed-out workers/resources safely, and test slow/trickling responses
   plus event-loop responsiveness. No SQL transaction spans provider waits.
5. **Authoritative single-owner allowlist (high).** Current verifier accepts any
   allowlisted email with `email_verified`, including third-party addresses
   without `hd`; Google warns it is not authoritative for those addresses in
   [its verification guide](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token).
   Restrict to Gmail or explicitly verified/configured Workspace hosted-domain
   claims (consistent with upstream) or pin an explicit stable subject securely.
   Existing sessions must also respect the current configured allowlist; changing
   that setting must not leave prior owner sessions authorized until expiry.
6. **Credential transport configuration (medium).** Google Cloud Run auth mode
   validates the service audience but does not require HTTPS for
   `PERSONAL_AI_BASE_URL`; credentials could be sent over cleartext. Fail closed
   for unsafe transport and verify outbound credentials are sent only to the
   configured intended service, including custom-audience policy in ADR.

## Acceptance coverage

Add a repeatable synthetic mounted browser sign-in/session → typed trip CRUD →
logout → expired/missing session check, with no live Google call. Include
CSRF rejection, stale-cookie recovery, private server-page protection and
unknown sign-out outcome behavior. Preserve server-only user/service tokens and
default-off AI/private-input gates. Test configuration/cookie-name coherence:
backend allows configurable names while frontend currently hardcodes defaults;
either constrain names or propagate a safe consistent configuration.

Re-run appropriate complete checks and record exact commits/evidence. No live
OAuth, IAM, cloud or private-data enablement is implied. Coordinator independently
re-reviews before the secure source/extraction stage.
