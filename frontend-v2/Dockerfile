# =============================================================================
# BNK-Forge Frontend — Optimized Dockerfile
# =============================================================================
#
# OPT-001: BuildKit cache mounts for npm ci (~335MB node_modules).
# First build: full npm ci (~60-90s). Cached builds: ~5s (cache hit).
#
# syntax=docker/dockerfile:1

# Build stage
# node:20-alpine — digest-pinned (Slice B / #300); re-pin when bumping the tag.
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS build
WORKDIR /app
# Copy VERSION file from repo root
COPY VERSION ./VERSION
# Copy package files first (changes less often → better layer caching)
COPY frontend-v2/package.json frontend-v2/package-lock.json ./
# OPT-001: BuildKit cache mount for npm — survives across builds
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY frontend-v2/ .
# Clear VITE_API_URL so production build uses relative URLs via proxy
# This ensures the app works when accessed from any IP, not just localhost
ENV VITE_API_URL=""
ENV VITE_WS_URL=""
RUN npm run build

# Runtime stage — tiny nginx image (~40MB)
# Slice 3 / #306: nginx-unprivileged — runs as uid 101 (non-root), listens on 8080.
# nginxinc/nginx-unprivileged:1.27-alpine — digest-pinned (2026-06-29); re-pin when bumping the tag.
FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0
# Slice D / #300: patch base OS packages. The pinned base ships openssl 3.3.3-r0 with the
# CRITICAL CVE-2026-31789; `apk upgrade` pulls the fixed 3.3.7-r0 — keeps the Trivy CRITICAL gate green.
# Must run as root for apk — the image drops back to USER nginx after setup.
USER root
RUN apk --no-cache upgrade
RUN rm -f /etc/nginx/conf.d/default.conf
COPY frontend-v2/nginx.conf /etc/nginx/conf.d/
COPY --from=build /app/dist /usr/share/nginx/html/
COPY frontend-v2/docker/99-brand-config.sh /docker-entrypoint.d/99-brand-config.sh
RUN chmod +x /docker-entrypoint.d/99-brand-config.sh
# Restore nginx file ownership so uid 101 can serve files
RUN chown -R nginx:nginx /usr/share/nginx/html
# Drop back to the unprivileged nginx user (uid 101)
USER nginx
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
