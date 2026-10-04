import { redirect } from "next/navigation";

import TripWorkspace from "../../../components/trip-workspace";
import { lookupPageSession } from "../../../lib/server-auth";

export const dynamic = "force-dynamic";

export default async function TripPage({ params }: { params: Promise<{ tripId: string }> }) {
  const status = await lookupPageSession();
  if (status.kind === "unauthenticated") redirect("/sign-in");
  if (status.kind === "unavailable") redirect("/sign-in?auth=unavailable");
  const { tripId } = await params;
  return <TripWorkspace key={tripId} tripId={tripId} />;
}
