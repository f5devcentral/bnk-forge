# Secrets Directory

This directory is for storing sensitive credentials that should NOT be committed to git.

**This directory is gitignored** - files placed here will not be tracked.

## Required Files

### F5 FAR Credentials (for BNK deployments)

**File:** `far-credentials.json`

This file contains your F5 Artifact Registry (FAR) service account credentials.
It's required for the `bnk/far-setup` module to authenticate with repo.f5.com.

**How to obtain:**
1. Log in to [MyF5](https://my.f5.com/manage/s/)
2. Navigate to **Resources** > **Downloads**
3. Accept the EULA, select **BIG-IP_Next** > **BIG-IP Next for Kubernetes**
4. Download the `f5-far-auth-key.tar` file
5. Extract and copy the base64 key file (e.g., `cne_pull_64.json`) to this directory:
   ```bash
   tar -xf f5-far-auth-key.tar
   cp cne_pull_64.json secrets/far-credentials.json
   ```

**IMPORTANT:** The FAR key file is already base64-encoded. Do NOT decode it!
It should be used as-is per F5 documentation:
```bash
cat far-credentials.json | helm registry login -u _json_key_base64 --password-stdin https://repo.f5.com
```

**Expected format:** The file contains a base64-encoded JSON service account key (single line of base64 text).

**Usage in BNK-Forge:**
When deploying the `bnk/far-setup` module, the platform will automatically use
`/app/secrets/far-credentials.json` as the `service_account_key_file` variable.

## Other Secrets

You can store other sensitive files here as needed:
- AWS credentials (if not using environment variables)
- TLS certificates
- SSH keys for private module repositories

## Security Notes

- Never commit this directory or its contents to git
- Keep backups of your credentials in a secure location
- Rotate credentials regularly
- Use different credentials for dev/staging/production
