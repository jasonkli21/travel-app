export const SESSION_COOKIE: string;
export const CSRF_COOKIE: string;
export const OAUTH_FLOW_COOKIE: string;
export const AI_USER_TOKEN_COOKIE: string;

export function sessionModeAllowed(
  sessionMode: "local" | "google_oidc",
  deploymentMode?: "local" | "hosted" | string,
): boolean;

export function authCallbackCookieHeader(request: Request): string | null;
export function validatedPublicOrigin(request: Request, allowedHosts: string[]): string | null;
export function responseAuthCookies(response: Response): string[];
export function googleAuthorizationUrl(
  value: string,
  options: { clientId: string; redirectUri: string },
): string | null;
export function authFailureLocation(status: number): string;
