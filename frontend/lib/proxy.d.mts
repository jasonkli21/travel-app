export function proxyRequest(
  request: Request,
  path: string[],
  options?: { backendBaseUrl?: string; allowedHosts?: string[]; fetchImpl?: typeof fetch },
): Promise<Response>;
