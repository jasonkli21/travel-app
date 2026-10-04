import { notFound, redirect } from "next/navigation";

import ProposalLifecycleFixture from "../../components/trip-workspace/proposal-lifecycle-fixture";
import { lookupPageSession } from "../../lib/server-auth";

export const dynamic = "force-dynamic";

export default async function ProposalFixturePage() {
  if (
    process.env.NODE_ENV !== "development"
    || process.env.TRAVEL_PROPOSAL_BROWSER_FIXTURE !== "true"
  ) {
    notFound();
  }
  const status = await lookupPageSession();
  if (status.kind === "unauthenticated") redirect("/sign-in");
  if (status.kind === "unavailable") redirect("/sign-in?auth=unavailable");

  return <ProposalLifecycleFixture />;
}
