import { readProxyBody } from "../../../../lib/proxy-response.mjs";

const backendBaseUrl = () =>
  (process.env.TRAVEL_API_URL ?? "http://localhost:8000").trim().replace(/\/+$/, "");

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const upstreamUrl = `${backendBaseUrl()}/v1/${path.map((part) => encodeURIComponent(part)).join("/")}${
    new URL(request.url).search
  }`;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }

  try {
    const response = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
      cache: "no-store",
      signal: AbortSignal.timeout(45_000),
    });
    const responseHeaders = new Headers();
    const upstreamContentType = response.headers.get("content-type");
    if (upstreamContentType) {
      responseHeaders.set("content-type", upstreamContentType);
    }
    return new Response(await readProxyBody(response), {
      status: response.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      {
        error: {
          code: "travel_api_unavailable",
          message: "The travel API is unavailable. Start the local backend and try again.",
          details: null,
        },
      },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
