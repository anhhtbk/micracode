/**
 * Environment accessor that resolves the API base URL differently on
 * server vs browser:
 *
 *   - Browser → `NEXT_PUBLIC_API_BASE_URL` (default "/api"). Requests go
 *     to the same origin as the Next.js app and are proxied internally
 *     by `next.config.ts` rewrites → `INTERNAL_API_URL` (docker network).
 *     The API never has to be exposed to the public internet.
 *   - Server (RSC / route handlers) → `INTERNAL_API_URL` directly
 *     (e.g. http://api:8000 inside docker), because relative paths
 *     have no origin in a node-side fetch.
 */

const isServer = typeof window === "undefined";

function resolveBaseUrl(): string {
  if (isServer) {
    // 127.0.0.1 (not "localhost") so Node 18+ doesn't resolve to ::1
    // while the dev uvicorn binds 127.0.0.1.
    return (
      process.env.INTERNAL_API_URL ??
      process.env.NEXT_PUBLIC_API_BASE_URL ??
      "http://127.0.0.1:8000"
    );
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";
}

export const env = {
  API_BASE_URL: resolveBaseUrl(),
} as const;

export type Env = typeof env;
