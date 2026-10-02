import { NextResponse } from "next/server";

export async function GET() {
  const apiUrl = process.env.TRAVEL_API_URL ?? "http://localhost:8000";
  try {
    const response = await fetch(`${apiUrl}/health`, { cache: "no-store" });
    const payload = await response.json();
    return NextResponse.json(payload, { status: response.status });
  } catch {
    return NextResponse.json(
      { status: "error", service: "travel-web", dependency: "travel-api" },
      { status: 503 }
    );
  }
}
