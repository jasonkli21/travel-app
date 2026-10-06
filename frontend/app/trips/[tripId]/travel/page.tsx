import { redirect } from "next/navigation";

import TripTravelMode from "../../../../components/trip-travel-mode";
import { lookupPageSession } from "../../../../lib/server-auth";

export const dynamic = "force-dynamic";

export default async function TripTravelPage({
  params,
}: {
  params: Promise<{ tripId: string }>;
}) {
  const status = await lookupPageSession();
  if (status.kind === "unauthenticated") redirect("/sign-in");
  if (status.kind === "unavailable") redirect("/sign-in?auth=unavailable");
  const { tripId } = await params;
  return <TripTravelMode key={tripId} tripId={tripId} />;
}
