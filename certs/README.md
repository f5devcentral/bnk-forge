# Custom TLS CA Certificates

Drop any additional TLS certificate authority (CA) certificates you need the
BNK-Forge backend containers to trust.

## Why this exists

Many corporate networks inspect outbound HTTPS traffic with a proxy
(e.g. Netskope, Zscaler, Blue Coat). The proxy presents re-signed certificates
for sites like GitHub, Docker Hub, and cloud APIs. Those certificates are signed
by an internal corporate CA that is **not** included in the public
`ca-certificates` package installed in the container image.

Without the corporate CA in the container trust store, git clones, Helm chart
downloads, and cloud API calls fail with errors such as:

```
server verification failed: certificate signer not trusted
```

## Usage

1. Obtain your corporate proxy's root CA certificate (usually a `.crt` or
   `.pem` file). Your IT/security team can provide this, or you can extract it
   from the TLS handshake of any intercepted site.
2. Copy the certificate file into this directory:

   ```bash
   cp /path/to/corporate-ca.crt certs/
   ```

3. Restart the BNK-Forge containers:

   ```bash
   docker compose down
   docker compose up -d
   ```

On startup, `backend/entrypoint.sh` copies any `.crt` or `.pem` files from
`/app/certs` into the system CA store and regenerates the certificate bundle.

## Security note

- This directory is mounted read-only into the containers.
- Certificate files placed here are **not committed to git** (see `.gitignore`).
- Do not share your organization's private CA certificate in public repositories.
