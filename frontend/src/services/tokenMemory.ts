/**
 * In-memory access-token cache for Authorization headers.
 * Refresh/access tokens are primarily kept in httpOnly cookies — never localStorage.
 */
let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function clearAccessToken(): void {
  accessToken = null;
}
