export function proposalFootprintMatches(proposal) {
  const base = [...proposal.base_place_revisions]
    .sort((left, right) => left.place_id.localeCompare(right.place_id));
  const current = [...proposal.current_place_revisions]
    .sort((left, right) => left.place_id.localeCompare(right.place_id));
  return base.length === current.length && base.every((item, index) =>
    item.place_id === current[index]?.place_id && item.revision === current[index]?.revision,
  );
}

export function proposalPresentationState(proposal, currentTripRevision, now = Date.now()) {
  if (!proposal) return null;
  if (proposal.lifecycle_state !== "ready") return proposal.state;
  if (proposal.state === "stale" || proposal.state === "expired") return proposal.state;
  if (proposal.base_trip_revision !== currentTripRevision || !proposalFootprintMatches(proposal)) {
    return "stale";
  }
  if (!proposal.expires_at || Date.parse(proposal.expires_at) <= now) return "expired";
  return "ready";
}

export function proposalCanApply(proposal, currentTripRevision, now = Date.now()) {
  return proposalPresentationState(proposal, currentTripRevision, now) === "ready"
    && proposal?.lifecycle_state === "ready"
    && proposal.current_trip_revision === currentTripRevision;
}

export function proposalIsTerminal(proposal, currentTripRevision) {
  return ["failed", "applied", "rejected", "stale", "expired"]
    .includes(proposalPresentationState(proposal, currentTripRevision) ?? "");
}
