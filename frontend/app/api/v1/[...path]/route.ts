import { proxyRequest } from "../../../../lib/proxy.mjs";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  return proxyRequest(request, path, {
    backendBaseUrl: process.env.TRAVEL_API_URL ?? "http://localhost:8000",
    deploymentMode: process.env.TRAVEL_DEPLOYMENT_MODE
      ?? (process.env.NODE_ENV === "production" ? "hosted" : "local"),
    allowedHosts: (process.env.TRAVEL_WEB_ALLOWED_HOSTS ?? "localhost,127.0.0.1,[::1]")
      .split(",").map((host) => host.trim().toLowerCase()),
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
