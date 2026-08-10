# Tier 2 — Infrastructure Verification Tests

These E2E tests deploy **real infrastructure** (VPC, Security Groups, EKS clusters)
and verify resources in AWS. They are expensive, slow (~30-60 min), and require
valid AWS credentials.

## Requirements

- Running Docker Compose stack (`docker compose up -d`)
- AWS credentials configured (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
- Optional: `AWS_SESSION_TOKEN` for assumed roles

## How to Run

```bash
# From repo root
make test-e2e-tier2

# Or directly
cd tests/e2e && E2E_TIER=2 npx playwright test tests/tier2/

# With AWS verification
cd tests/e2e && VERIFY_AWS=true E2E_TIER=2 npx playwright test tests/tier2/

# Skip cleanup (leave resources for inspection)
cd tests/e2e && SKIP_CLEANUP=true E2E_TIER=2 npx playwright test tests/tier2/
```

## ⚠️ Warning

These tests create **real AWS resources** that incur costs.
Always ensure cleanup runs or manually delete resources after testing.

## Known Issues

- The "Add Module" dialog has a Radix UI viewport issue where the "Next" button
  may be outside the Playwright viewport. This causes Step 2+ to fail.
  See `E2E_TEST_ISSUES.md` in the parent directory for details and workarounds.
