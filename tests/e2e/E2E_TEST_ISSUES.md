# E2E Test Issues - 2026-01-22

## Summary

E2E tests are currently blocked on **Step 2: Add VPC Module** due to UI interaction issues with the Add Module dialog.

**Status:**
- ✅ Step 1: Create Project - **PASSES**
- ❌ Step 2: Add VPC Module - **FAILS** (blocking all remaining tests)

---

## Current Blocking Issue

### Problem: "Next" Button Click Fails

**Location:** `tests/e2e/pages/project-detail.page.ts` - `addModule()` method

**Error:**
```
TimeoutError: locator.click: Timeout 30000ms exceeded.
Element is outside of the viewport
```

**What Works:**
1. ✅ Dialog opens successfully
2. ✅ Module search works
3. ✅ Module card is found and clicked (VPC module gets selected)
4. ❌ Next button cannot be clicked - consistently reports "element is outside of the viewport"

**What Was Tried (All Failed):**
1. ❌ Regular click with `scrollIntoViewIfNeeded()` - element still outside viewport
2. ❌ Force click with `{ force: true }` - still fails ("element outside viewport" overrides force)
3. ❌ Manual scroll using `evaluate()` to scroll dialog container - didn't help
4. ❌ JavaScript click via `evaluate()` - still timing out (last attempt)

**Root Cause Hypothesis:**
The AddModuleToProjectDialog uses a Radix UI Dialog component with complex scrolling behavior. The "Next" button appears to be positioned in a way that Playwright consistently cannot interact with it, even though it's visible to the user. This suggests:
- Dialog has a scroll container that Playwright's viewport detection doesn't recognize
- Button positioning may be affected by CSS transforms or absolute positioning
- Radix Dialog's internal structure may be interfering with Playwright's click detection

---

## Test Code Issues Fixed

### 1. Module Card Selector ✅ FIXED
**Problem:** Test was looking for `[data-testid="module-card"]` which doesn't exist in the actual component.

**Solution:** Updated selector to match module buttons by name:
```typescript
const moduleCard = this.page.locator(`[role="dialog"] button`).filter({
  has: this.page.locator('div.font-medium').filter({ hasText: new RegExp(`^${moduleName}$`, 'i') })
}).first();
```

**File:** `tests/e2e/pages/project-detail.page.ts:80-85`

### 2. Module Name Matching ✅ FIXED
**Problem:** Using `:has-text("vpc")` matched both VPC and EKS modules (EKS description contains "VPC CNI").

**Solution:** Use regex to match exact module name in the `div.font-medium` element.

---

## Actual UI Flow (From Error Context)

When adding a module from Project Detail page:

1. Click "Add Module" button → Dialog opens
2. Dialog shows module library with search
3. User types in search box → filters modules
4. User clicks module card → module gets selected
5. **Dialog shows 3-step wizard:**
   - Step 1: Select Project (already selected since we're on project detail page)
   - Step 2: Configure
   - Step 3: Confirm
6. User clicks "Next" button → **THIS IS WHERE IT FAILS**
7. Dialog should show configuration screen
8. User fills variables
9. User clicks "Add Module" to confirm

---

## Recommended Solutions

### Option 1: Fix the Dialog UI (Frontend Change)
**File:** `frontend-v2/src/components/modules/AddModuleToProjectDialog.tsx`

The dialog may need layout adjustments:
- Ensure Next button is always in viewport
- Check scroll container behavior
- Review Radix Dialog configuration

### Option 2: Alternative Test Approach
Instead of using the Add Module dialog, test via direct API calls:

```typescript
// Create project via UI (Step 1 already works)
// Then add modules via API instead of UI
await this.page.evaluate(async ({ projectId, moduleLibraryId }) => {
  const response = await fetch(`/api/project-modules`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId,
      module_library_id: moduleLibraryId,
      variables: { vpc_cidr: '10.0.0.0/16' }
    })
  });
  return response.json();
}, { projectId, moduleLibraryId: 1 });
```

### Option 3: Simplify Test - Skip Module Addition Dialog
Focus E2E tests on the core OpenTofu execution flow rather than UI interactions:
- Create project via UI
- Add modules via API
- Test Init/Plan/Apply via UI (the critical OpenTofu functionality)
- Destroy via UI
- Delete project via UI

This would unblock testing the actual platform functionality (OpenTofu execution, Celery tasks, AWS verification).

---

## Files Modified

### `tests/e2e/pages/project-detail.page.ts`
**Lines 73-109:** Updated `addModule()` method with:
- Better module card selector
- Module name regex matching
- Multiple attempts to click Next button (all failed)

**Current State:** Contains JavaScript evaluate() click attempt as last resort.

---

## Next Steps for Future Agent

1. **Quick Win:** Implement Option 3 (API-based module addition for tests)
   - Keeps UI test for project creation
   - Uses API to add modules (faster, more reliable)
   - Tests the important parts (OpenTofu execution) via UI

2. **Proper Fix:** Investigate `AddModuleToProjectDialog.tsx` layout issues
   - Check why Next button is outside viewport
   - Review Radix Dialog scroll container setup
   - May need to adjust dialog max-height or button positioning

3. **Alternative:** Use Playwright's debug mode to manually test
   ```bash
   npx playwright test --debug --grep "Step 2"
   ```
   This allows stepping through and seeing exactly what's happening.

---

## Test Execution Commands

```bash
# Run full suite
cd tests/e2e
npx playwright test --reporter=line

# Run specific step
npx playwright test --grep "Step 2"

# Debug mode
npx playwright test --debug --grep "Step 2"

# View trace
npx playwright show-trace test-results/.../trace.zip
```

---

**Last Updated:** 2026-01-22
**Agent:** Claude (Sonnet 4.5)
