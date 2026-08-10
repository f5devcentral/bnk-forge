# Slice 3 / #306: nginx-unprivileged — runs as uid 101 (non-root), listens on 8082/8443
# under host networking (proxy/nginx.conf); bridge/K8s configs keep 8080/8443
# since each has its own network namespace.
# nginxinc/nginx-unprivileged:1.27-alpine — digest-pinned (2026-06-29); re-pin when bumping the tag.
FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0

# Slice D / #300: patch base OS packages (openssl CVE-2026-31789 etc.), then install openssl
# for self-signed cert generation. The proxy terminates inbound TLS, so keep its openssl current.
# Must run as root for apk/openssl — switch back to nginx user afterwards.
USER root
RUN apk --no-cache upgrade && apk add --no-cache openssl

# Remove default nginx config
RUN rm -f /etc/nginx/conf.d/default.conf

# Generate self-signed TLS certificate (valid 10 years)
# In production, replace with a real cert via volume mount.
# Certs are world-readable so uid 101 (nginx) can read them at runtime.
RUN mkdir -p /etc/nginx/ssl && \
    openssl req -x509 -nodes -days 3650 \
      -newkey rsa:2048 \
      -keyout /etc/nginx/ssl/server.key \
      -out /etc/nginx/ssl/server.crt \
      -subj "/CN=bnk-forge/O=BNK-Forge/C=AU" && \
    chmod 644 /etc/nginx/ssl/server.crt && \
    chmod 640 /etc/nginx/ssl/server.key && \
    chown -R nginx:nginx /etc/nginx/ssl

# Copy our proxy config
COPY nginx.conf /etc/nginx/conf.d/

# Drop back to the unprivileged nginx user (uid 101)
USER nginx

EXPOSE 8082 8443
CMD ["nginx", "-g", "daemon off;"]
