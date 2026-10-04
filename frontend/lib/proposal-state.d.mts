import type { ProposalDetail, ProposalState } from "./api";

export function proposalFootprintMatches(proposal: ProposalDetail): boolean;
export function proposalPresentationState(
  proposal: ProposalDetail | null,
  currentTripRevision: number,
  now?: number,
): ProposalState | null;
export function proposalCanApply(
  proposal: ProposalDetail | null,
  currentTripRevision: number,
  now?: number,
): boolean;
export function proposalIsTerminal(
  proposal: ProposalDetail | null,
  currentTripRevision: number,
): boolean;
