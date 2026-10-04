import { notFound } from "next/navigation";

import ProposalLifecycleFixture from "../../components/trip-workspace/proposal-lifecycle-fixture";

export const dynamic = "force-dynamic";

export default function ProposalFixturePage() {
  if (
    process.env.NODE_ENV !== "development"
    || process.env.TRAVEL_PROPOSAL_BROWSER_FIXTURE !== "true"
  ) {
    notFound();
  }

  return <ProposalLifecycleFixture />;
}
