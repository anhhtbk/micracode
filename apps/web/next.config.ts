import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

/**
 * Headers required for StackBlitz WebContainers (Phase 3).
 *
 * COEP `require-corp` + COOP `same-origin` put the app in a
 * "cross-origin isolated" state so that `SharedArrayBuffer` is
 * available — WebContainers needs it.
 *
 * Consequence to remember:
 *   - Third-party `<img>`, `<script>`, `<iframe>` must send
 *     `Cross-Origin-Resource-Policy: cross-origin` (or be same-origin).
 */
const securityHeaders = [
  { key: "Cross-Origin-Embedder-Policy", value: "require-corp" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

// __dirname is not defined in ESM; reconstruct it for `outputFileTracingRoot`.
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Toggle Next.js standalone output via env (default: ON for Docker images).
// Set NEXT_STANDALONE=false to disable (e.g. when debugging file tracing).
const standaloneEnabled = (process.env.NEXT_STANDALONE ?? "true") !== "false";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  experimental: {
    typedRoutes: true,
  },
  transpilePackages: ["@micracode/shared", "@webcontainer/api"],
  ...(standaloneEnabled
    ? {
        output: "standalone" as const,
        // Monorepo: trace from repo root so packages/shared gets bundled.
        outputFileTracingRoot: path.join(__dirname, "../../"),
      }
    : {}),
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
