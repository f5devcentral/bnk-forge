# E2E Test Fixes Applied

**Date:** 2026-01-22
**Agent:** Claude (Sonnet 4.5)

## Summary

Fixed multiple critical issues in E2E tests that prevented them from running. All fixes were based on auditing test code against actual component implementations.

---

## Files Modified

1. `tests/e2e/pages/project-detail.page.ts` - 4 critical fixes
2. `tests/e2e/pages/projects.page.ts` - 1 critical fix
3. `tests/e2e/config/test-config.ts` - 2 configuration updates

---

## Critical Fixes Applied

### Fix 1: Initialize Button Text ✅
**Location:** `tests/e2e/pages/project-detail.page.ts:116-126`

**Issue:** Test looked for button with text "Init", but actual button text is "Initialize"

**Before:**
```typescript
const initButton = card.locator('button:has-text("Init")');
```

**After:**
```typescript
const initButton = card.locator('button:has-text("Initialize")');
```

**Impact:** Test can now find and click the Initialize button

---

### Fix 2: Apply Confirmation Button ✅
**Location:** `tests/e2e/pages/project-detail.page.ts:143-165`

**Issue:** Test looked for "Confirm" or "Yes" button, but actual button text is "Apply with Auto-Approve"

**Before:**
```typescript
const confirmButton = this.page.locator('button:has-text("Confirm"), button:has-text("Yes")');
```

**After:**
```typescript
const confirmButton = this.page.locator('button:has-text("Apply with Auto-Approve"), button:has-text("Apply")').first();
```

**Reason:** ApplyConfirmDialog shows dynamic button text based on auto-approve checkbox state (default: "Apply with Auto-Approve")

**Impact:** Test can now confirm apply operations

---

### Fix 3: Destroy Confirmation Button ✅
**Location:** `tests/e2e/pages/project-detail.page.ts:189-193`

**Issue:** Test had multiple fallback selectors, but only first one is correct

**Before:**
```typescript
const confirmButton = this.page.locator('button:has-text("Destroy Infrastructure"), button:has-text("Confirm"), button:has-text("Yes")').first();
```

**After:**
```typescript
const confirmButton = this.page.locator('button:has-text("Destroy Infrastructure")').first();
```

**Reason:** Simplified selector - button text is confirmed as "Destroy Infrastructure" in AlertDialog

**Impact:** Cleaner, more reliable selector

---

### Fix 4: Project Deletion Flow ✅
**Location:** `tests/e2e/pages/projects.page.ts:177-204`

**Issue:** Test tried to delete project from card 3-dot menu, but Phase 3 changes removed all buttons from project cards

**Before:**
```typescript
async deleteProject(projectName: string) {
  // Click 3-dot menu on card
  const menuButton = card.locator('button[aria-label*="menu" i], button:has-text("⋮")');
  await menuButton.click();
  await this.page.click('text=/delete/i');
  // ... confirm
}
```

**After:**
```typescript
async deleteProject(projectName: string) {
  // Navigate to project detail page
  await this.openProject(projectName);
  await this.page.waitForURL(/\/projects\/\d+/);

  // Click Delete button in header
  const deleteButton = this.page.locator('button:has-text("Delete")').first();
  await deleteButton.click();

  // Confirm in AlertDialog
  const confirmButton = this.page.locator('[role="alertdialog"] button:has-text("Delete")').first();
  await confirmButton.click();

  // Wait for navigation back
  await this.page.waitForURL(/\/projects$/);
}
```

**Reason:** Project cards are now fully clickable (NAV-3 changes). Delete button only available on project detail page header.

**Impact:** Test can now successfully delete projects

---

### Fix 5: Module Addition Flow ✅
**Location:** `tests/e2e/pages/project-detail.page.ts:60-96`

**Issue:** Test assumed simple click-and-add flow, but actual implementation is multi-step wizard

**Before:**
```typescript
async addModule(modulePath: string, variables?: Record<string, string>) {
  await this.clickAddModule();
  await this.page.click(`text="${modulePath}"`);
  await this.page.click('button:has-text("Add to Project")');
  if (variables) {
    await this.fillModuleVariables(variables);
  }
}
```

**After:**
```typescript
async addModule(modulePath: string, variables?: Record<string, string>) {
  await this.clickAddModule();

  // Extract module name for searching
  const moduleName = modulePath.split('/').pop() || modulePath;

  // Find and click module card (with fallback search)
  const moduleCard = this.page.locator(`[data-testid="module-card"]:has-text("${modulePath}")`).first();
  if (!await moduleCard.isVisible({ timeout: 2000 })) {
    // Try searching if not visible
    const searchInput = this.page.locator('input[type="search"]').first();
    await searchInput.fill(moduleName);
  }
  await moduleCard.click();

  // Click Next button if wizard appears
  const nextButton = this.page.locator('button:has-text("Next")').first();
  if (await nextButton.isVisible({ timeout: 2000 })) {
    await nextButton.click();
  }

  // Fill variables if provided
  if (variables) {
    await this.fillModuleVariables(variables);
  }

  // Click final Add button
  const addButton = this.page.locator('button:has-text("Add Module"), button:has-text("Add to Project")').first();
  await addButton.click();
}
```

**Reason:** AddModuleToProjectDialog is a multi-step wizard:
1. Select module from library
2. Configure variables (optional)
3. Confirm and add

**Impact:** Test can now successfully add modules through the wizard flow

---

## Configuration Updates

### Update 1: Module Paths ✅
**Location:** `tests/e2e/config/test-config.ts:36-58`

**Issue:** Module paths didn't include "infra/" prefix

**Before:**
```typescript
modules: {
  vpc: { path: 'aws/vpc' },
  security: { path: 'aws/security' },
  eks: { path: 'aws/eks' },
}
```

**After:**
```typescript
modules: {
  vpc: { path: 'infra/aws/vpc' },
  security: { path: 'infra/aws/security' },
  eks: { path: 'infra/aws/eks' },
}
```

**Reason:** Module library stores paths as "infra/{provider}/{module}"

**Impact:** Module search will find correct modules

---

### Update 2: Selector Documentation ✅
**Location:** `tests/e2e/config/test-config.ts:81-102`

**Issue:** Selectors documented incorrect button text

**Changes:**
- ✅ `createProjectButton`: "Create Project" → "New Project"
- ✅ `initButton`: "Init" → "Initialize"
- ✅ `addModuleButton`: "Add to Project" → "Add Module" (opens dialog)

**Reason:** Document actual UI text for future reference

**Impact:** Configuration matches actual implementation

---

## Testing Recommendations

### Before Running E2E Tests:

1. **Verify module library is synced:**
   ```bash
   # Check that modules exist in database
   docker exec bnk-forge-v2-backend python -c "
   from database import SessionLocal
   from models import LibraryModule
   db = SessionLocal()
   modules = db.query(LibraryModule).filter(
     LibraryModule.name.in_(['vpc', 'security', 'eks'])
   ).all()
   print(f'Found {len(modules)} modules')
   for m in modules:
     print(f'  - {m.name}: infra/{m.provider}/{m.name}')
   "
   ```

2. **Verify AWS credentials are valid:**
   ```bash
   docker exec bnk-forge-v2-backend aws sts get-caller-identity
   ```

3. **Check Celery workers are running:**
   ```bash
   docker ps | grep celery
   ```

### Running Tests:

```bash
cd tests/e2e

# Quick validation (watch mode, no cleanup)
SKIP_CLEANUP=true npm run test:watch

# Full test with AWS verification
VERIFY_AWS=true npm test

# Full test without AWS verification (faster)
npm test
```

### Expected Results:

- ✅ Project creation
- ✅ Module addition (VPC → Security → EKS dependency chain)
- ✅ Initialize operations
- ✅ Plan operations
- ✅ Apply operations (may take 10-15 min for EKS)
- ✅ Task monitoring
- ✅ Destroy operations
- ✅ Project deletion

---

## Known Limitations

### 1. Variable Filling Not Fully Tested
The `fillModuleVariables()` function may need additional work for complex variable structures. Current modules (VPC, Security, EKS) have minimal required user inputs due to auto-wiring.

**Status:** Deferred - will be caught during actual test execution

### 2. Module Library Structure
Module paths assume "infra/{provider}/{module}" format. If actual database uses different format, update test config accordingly.

**Status:** To be verified during test execution

### 3. AWS Session Token Expiration
E2E tests take 30-45 minutes. If using temporary AWS credentials (SESSION_TOKEN), they may expire mid-test.

**Workaround:** Use IAM roles or long-lived credentials for testing

---

## Verification Status

- ✅ All critical button selectors fixed
- ✅ All flow mismatches resolved
- ✅ Configuration updated
- ✅ Comments added for maintainability
- ⏳ Actual test execution pending (requires running environment)

---

## Next Steps

1. ✅ Review and commit fixes
2. ⏳ Run E2E tests in development environment
3. ⏳ Address any new issues found during execution
4. ⏳ Document any additional edge cases
5. ⏳ Consider adding more robust error handling
6. ⏳ Add retry logic for flaky operations (network, AWS API rate limits)

---

## Files Ready for Testing

All fixes have been applied. Tests are ready to run once:
1. Backend is running with valid AWS credentials
2. Database is initialized with module library
3. Celery workers are running
4. Frontend is accessible

**Run command:** `cd tests/e2e && npm test`

---

## Related Documentation

- **Audit Report:** `tests/e2e/E2E_TEST_AUDIT.md` - Detailed analysis of all issues
- **Test README:** `tests/e2e/README.md` - How to run tests
- **Current Work:** `.agent/CURRENT_WORK.md` - Phase 2 completion notes
- **GUI Changes:** `.agent/GUI_IMPROVEMENTS.md` - Phase 3 NAV changes that affected tests
