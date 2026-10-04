# Proposal lifecycle browser fixture

The local browser fixture mounts the real `ProposalPanel` against deterministic
in-memory API methods. It never contacts the Travel API or `personal-ai-system`.

Start the frontend from `frontend/`:

```sh
TRAVEL_PROPOSAL_BROWSER_FIXTURE=true \
NEXT_PUBLIC_TRAVEL_PROPOSALS_ENABLED=true \
pnpm dev
```

Open `http://127.0.0.1:3000/proposal-fixture/` and use these scenarios:

1. **Expiry while preview is open:** leave the default scenario selected,
   generate a preview, wait four seconds, and confirm the state changes to
   expired and **Apply proposal** disables without a server refresh.
2. **Stale before apply:** select the stale scenario, generate a preview, then
   try to apply it. The detail refresh returns a stale revision and the
   itinerary is not applied.
3. **Lost apply response and failed refresh:** select that scenario, generate a
   preview, apply it, and confirm the fixture reports that the stored result
   was applied, workspace reload is required, and the apply action stays
   closed.

The route returns 404 unless both the development server and the explicit
fixture environment flag are active.
