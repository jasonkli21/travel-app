"use client";

import { useParams } from "next/navigation";

import TripWorkspace from "../../../components/trip-workspace";

export default function TripPage() {
  const params = useParams<{ tripId: string }>();
  return <TripWorkspace key={params.tripId} tripId={params.tripId} />;
}
