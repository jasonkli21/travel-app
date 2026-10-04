import { redirect } from "next/navigation";

import HomeWorkspace from "../components/home-workspace";
import { lookupPageSession } from "../lib/server-auth";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const status = await lookupPageSession();
  if (status.kind === "unauthenticated") redirect("/sign-in");
  if (status.kind === "unavailable") redirect("/sign-in?auth=unavailable");
  return <HomeWorkspace />;
}
