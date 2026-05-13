# syntax=docker/dockerfile:1.7
# Multi-stage build for the Next.js web app (monorepo: apps/web + packages/shared).
# Build context = repo root.
#
# Build args:
#   NEXT_STANDALONE        "true" (default) → uses Next.js `output: "standalone"`,
#                          giving a much smaller runtime image. Set to "false"
#                          to fall back to copying full node_modules.
#   NEXT_PUBLIC_API_BASE_URL  Baked into the client bundle at build time
#                             (NEXT_PUBLIC_* vars are inlined by Next).

ARG BUN_VERSION=1.3.11
ARG NODE_VERSION=22.18.0

############################
# 1) deps: install workspace deps with Bun
############################
FROM oven/bun:${BUN_VERSION} AS deps
WORKDIR /repo

COPY package.json bunfig.toml ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/shared/package.json ./packages/shared/package.json
COPY bun.lock* bun.lockb* ./

RUN bun install --frozen-lockfile || bun install

############################
# 2) builder: copy sources and build Next.js
############################
FROM oven/bun:${BUN_VERSION} AS builder
WORKDIR /repo

ARG NEXT_STANDALONE=true
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_STANDALONE=${NEXT_STANDALONE} \
    NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

COPY --from=deps /repo/node_modules ./node_modules
COPY . .

RUN bun --filter web build

############################
# 3a) runner-standalone: minimal image (Next standalone output)
############################
FROM node:${NODE_VERSION}-slim AS runner-standalone
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN groupadd --system --gid 1001 nodejs \
 && useradd  --system --uid 1001 --gid nodejs nextjs

# Standalone output (with outputFileTracingRoot = repo root) preserves the
# monorepo layout inside .next/standalone/.
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next/static     ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/public           ./apps/web/public

USER nextjs
EXPOSE 3000
CMD ["node", "apps/web/server.js"]

############################
# 3b) runner-full: fallback runner using full node_modules + `next start`
############################
FROM node:${NODE_VERSION}-slim AS runner-full
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN groupadd --system --gid 1001 nodejs \
 && useradd  --system --uid 1001 --gid nodejs nextjs

COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/.next ./apps/web/.next
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/public ./apps/web/public
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/package.json ./apps/web/package.json
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/next.config.ts ./apps/web/next.config.ts
COPY --from=builder --chown=nextjs:nodejs /repo/apps/web/node_modules ./apps/web/node_modules
COPY --from=builder --chown=nextjs:nodejs /repo/node_modules ./node_modules
COPY --from=builder --chown=nextjs:nodejs /repo/packages ./packages
COPY --from=builder --chown=nextjs:nodejs /repo/package.json ./package.json

USER nextjs
EXPOSE 3000
WORKDIR /app/apps/web
CMD ["node", "node_modules/next/dist/bin/next", "start", "--port", "3000", "--hostname", "0.0.0.0"]

############################
# Final stage: pick standalone (default) or full via --target
#   docker build --target runner-standalone  (default behavior of compose)
#   docker build --target runner-full        (fallback)
############################
FROM runner-standalone AS final
