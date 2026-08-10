# bnk-forge Helm chart

Deploys BNK-Forge (api, worker, beat, frontend, proxy, mcp + Postgres + Redis)
to Kubernetes. Images are pulled from the registry configured via
`global.imageRegistry` (default: `ghcr.io/f5devcentral`).

## Quick start

```bash
kubectl create namespace bnk-forge

# Only needed for a private registry (e.g. an internal mirror). The default
# ghcr.io/f5devcentral images are public and need no pull secret.
kubectl -n bnk-forge create secret docker-registry registry-pull \
  --docker-server=<your-registry> \
  --docker-username=<your-user> \
  --docker-password=<your-token>

helm upgrade --install bnk-forge ./helm/bnk-forge \
  -n bnk-forge --create-namespace \
  --set global.imageRegistry=<your-registry> \
  --set global.imagePullSecrets[0].name=registry-pull \
  --set global.sharedStorageClass=<rwx-storage-class>
```

## Required cluster features

- **ReadWriteMany StorageClass** for shared backend/worker state. Examples:
  NFS, AWS EFS, Azure Files, GCP Filestore, CephFS, Longhorn (with RWX).
  Set `global.sharedStorageClass` accordingly.
- **Ingress controller or LoadBalancer** to expose the proxy externally.

## Exposing the UI

Three options, in order of preference:

1. **Ingress** — `--set ingress.enabled=true --set ingress.host=bnk.example.com`
2. **LoadBalancer** — `--set proxy.service.type=LoadBalancer`
3. **Port-forward** — `kubectl port-forward svc/<release>-proxy 8443:443`

## Secrets

If `secrets.*` values are empty on first install, the chart generates random
strings and reuses them on upgrade (lookup-based). Override explicitly for
production:

```yaml
secrets:
  postgresPassword: ...
  redisPassword: ...
  jwtSecretKey: ...
  encryptionKey: ...
  mcpPassword: ...
```

## Known caveats

- The `bnk-forge-proxy` image was built for docker-compose with
  `network_mode: host` and hardcoded `127.0.0.1` upstreams. The chart sets
  `NGINX_*_HOST` env vars but the upstream nginx.conf does not consume them
  out of the box. If proxy routing fails, rebuild the proxy image with a
  k8s-friendly `nginx.conf` (templated upstreams) or expose `api`, `frontend`,
  and `mcp` services directly via separate Ingress paths.
- `postgres-backup` cronjob from `docker-compose.yml` is not yet templated.
- `bnk-license`, `bnk-operator`, and CLI containers are not part of this
  chart — add them as needed.

## Smoke test

```bash
helm template bnk-forge ./helm/bnk-forge | kubectl apply --dry-run=client -f -
helm lint ./helm/bnk-forge
```

## Tested versions

- Kubernetes 1.27+
- Helm 3.13+
