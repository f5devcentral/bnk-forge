# BNK-Forge v2 - E2E Test Suite

Comprehensive end-to-end testing for BNK-Forge v2 using Playwright.

## Overview

This test suite validates the complete user workflow:

1. ✅ **Project Creation** - Create test project via GUI
2. ✅ **Module Addition** - Add VPC → Security → EKS (dependency chain)
3. ✅ **Dependency Validation** - Verify dependency status display
4. ✅ **Init/Plan/Apply** - Execute full OpenTofu workflow
5. ✅ **Task Monitoring** - Validate task history and logs
6. ✅ **AWS Verification** - Use AWS SDK to verify real resources
7. ✅ **Destroy & Cleanup** - Destroy modules and verify cleanup

## Prerequisites

### Required
- **Docker** - Application must be running (`docker-compose up -d`)
- **Node.js 20+** - For local test execution
- **AWS Credentials** - Configured in `.env` file

### Optional
- **Playwright Browsers** - Auto-installed on first run

## Installation

### Option 1: Local Execution (Recommended)

```bash
cd tests/e2e
npm install
npx playwright install chromium
```

### Option 2: Docker Execution

No installation needed - tests run in container.

## Running Tests

### Local Execution

```bash
# Run all tests (headless)
npm test

# Run with visible browser (watch tests execute)
npm run test:headed

# Debug mode (step through tests)
npm run test:debug

# Interactive UI mode
npm run test:ui

# Run with no cleanup (leave resources for inspection)
npm run test:no-cleanup

# Skip AWS verification (faster, no SDK calls)
npm run test:no-aws

# Run single test by name
npm run test:single -- --grep "Step 1"
```

### Docker Execution

```bash
cd tests/e2e

# Run tests in container
docker-compose -f docker-compose.test.yml up --build

# Run with no cleanup
SKIP_CLEANUP=true docker-compose -f docker-compose.test.yml up --build

# Run without AWS verification
VERIFY_AWS=false docker-compose -f docker-compose.test.yml up --build
```

## Test Configuration

Edit `config/test-config.ts` to customize:

```typescript
export const TEST_CONFIG = {
  // Application URLs
  baseUrl: 'https://localhost',
  apiUrl: 'http://localhost:8000',

  // Test behavior flags
  flags: {
    skipCleanup: false,      // Set true to leave resources
    verifyAwsResources: true, // Set false to skip AWS checks
    captureScreenshots: true, // Set false to disable screenshots
  },

  // Test timeouts (milliseconds)
  timeouts: {
    terraformInit: 120000,    // 2 minutes
    terraformPlan: 180000,    // 3 minutes
    terraformApply: 600000,   // 10 minutes
    terraformDestroy: 600000, // 10 minutes
  },

  // Module configuration
  modules: {
    vpc: {
      path: 'infra/aws/vpc',
      variables: { vpc_cidr: '10.0.0.0/16' },
    },
    // ... more modules
  },
};
```

## Environment Variables

Set these in your shell or `.env` file:

```bash
# Application URLs (defaults to localhost)
TEST_BASE_URL=https://localhost
TEST_API_URL=http://localhost:8000

# Test behavior flags
SKIP_CLEANUP=false          # true = leave resources for inspection
VERIFY_AWS=true             # false = skip AWS SDK verification
SCREENSHOTS=true            # false = disable screenshots

# AWS credentials (required for verification)
AWS_REGION=ap-southeast-2
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_SESSION_TOKEN=your_token  # If using SSO
```

## Test Output

### Reports

After test run, view results:

```bash
# Open HTML report in browser
npm run test:report

# View JSON results
cat test-results/results.json
```

### Screenshots

Screenshots saved to `test-results/`:
- `01-projects-page.png` - Initial state
- `02-project-created.png` - After project creation
- `03-vpc-module-added.png` - VPC module added
- ... and more for each step

### Videos

On failure, videos saved to `test-results/videos/`

## Test Structure

```
tests/e2e/
├── config/
│   └── test-config.ts          # Central configuration
├── fixtures/
│   └── aws-helper.ts            # AWS SDK utilities
├── pages/
│   ├── projects.page.ts         # Page object: Projects
│   ├── project-detail.page.ts  # Page object: Project Details
│   └── tasks.page.ts            # Page object: Tasks
├── tests/
│   └── 01-full-e2e-workflow.spec.ts  # Main E2E test
└── utils/
    └── (future utilities)
```

## Typical Test Execution Timeline

**Total Duration: ~30-45 minutes** (depending on AWS region and module complexity)

| Step | Operation | Duration |
|------|-----------|----------|
| 1-4 | Project + Module Setup | ~2 min |
| 5-7 | VPC Init/Plan/Apply | ~5 min |
| 9 | Security Init/Plan/Apply | ~5 min |
| 11 | EKS Init/Plan/Apply | ~15-20 min |
| 14-16 | Destroy All | ~5-10 min |

**Note:** EKS cluster creation is the longest operation (~15 min).

## Troubleshooting

### Tests Failing?

1. **Check application is running:**
   ```bash
   docker-compose ps
   # All containers should be "Up"
   ```

2. **Check backend/celery logs:**
   ```bash
   docker logs bnk-forge-backend
   docker logs bnk-forge-celery-worker
   ```

3. **Check AWS credentials:**
   ```bash
   # In .env file, verify:
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   AWS_SESSION_TOKEN=...  # If using SSO
   ```

4. **Run in debug mode:**
   ```bash
   npm run test:debug
   ```

### Common Issues

**Issue:** "Timeout waiting for task completion"
- **Cause:** OpenTofu operation taking longer than timeout
- **Fix:** Increase timeout in `test-config.ts`:
  ```typescript
  timeouts: {
    terraformApply: 900000,  // 15 minutes instead of 10
  }
  ```

**Issue:** "AWS credentials not found"
- **Cause:** Missing AWS environment variables
- **Fix:** Ensure AWS credentials in `.env` and exported:
  ```bash
  export AWS_ACCESS_KEY_ID=...
  export AWS_SECRET_ACCESS_KEY=...
  ```

**Issue:** "Element not found" or "Selector timeout"
- **Cause:** UI selectors changed, or page didn't load
- **Fix:** Update selectors in page objects or `test-config.ts`
- **Debug:** Run with `npm run test:headed` to watch execution

**Issue:** "VPC already exists"
- **Cause:** Previous test didn't clean up
- **Fix:** Manually delete VPC in AWS console, or change test VPC CIDR in config

## Adding New Tests

1. **Create test file** in `tests/`:
   ```typescript
   import { test, expect } from '@playwright/test';
   import { ProjectsPage } from '../pages/projects.page';

   test('My new test', async ({ page }) => {
     const projectsPage = new ProjectsPage(page);
     // ... test logic
   });
   ```

2. **Update page objects** if needed (in `pages/`)

3. **Run your test:**
   ```bash
   npm test -- tests/my-new-test.spec.ts
   ```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Start application
        run: docker-compose up -d

      - name: Run E2E tests
        working-directory: tests/e2e
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        run: |
          npm ci
          npm test

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: test-results
          path: tests/e2e/test-results
```

## Test Flags Reference

| Flag | Default | Description |
|------|---------|-------------|
| `SKIP_CLEANUP` | `false` | Skip destroy and project deletion (leave resources) |
| `VERIFY_AWS` | `true` | Use AWS SDK to verify resources |
| `SCREENSHOTS` | `true` | Capture screenshots at each step |
| `CI` | `false` | Enable CI mode (2 retries, strict reporting) |

## Best Practices

1. **Always run with SKIP_CLEANUP=false in CI** - Don't leave AWS resources
2. **Use SKIP_CLEANUP=true during development** - Inspect resources after test
3. **Monitor AWS costs** - EKS clusters are expensive, destroy promptly
4. **Check test-results/** for screenshots on failures
5. **Update timeouts** if your AWS region is slow

## Support

If tests fail consistently:
1. Check `.agent/CURRENT_WORK.md` for known issues
2. Check `docker logs` for backend/celery errors
3. Verify AWS credentials are valid and not expired
4. Run `npm run test:debug` to step through failing test

---

**Last Updated:** 2026-01-21
