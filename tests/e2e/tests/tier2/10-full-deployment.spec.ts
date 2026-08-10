/**
 * Full E2E Workflow Test
 *
 * Tests the complete user journey:
 * 1. Create project
 * 2. Add modules with dependency chain (VPC → Security → EKS)
 * 3. Init, Plan, Apply each module
 * 4. Verify AWS resources created
 * 5. Verify task/deployment screens
 * 6. Destroy all modules
 * 7. Verify AWS resources destroyed
 */

import { test, expect } from '@playwright/test';
import { LoginPage } from '../../pages/login.page';
import { ProjectsPage } from '../../pages/projects.page';
import { ProjectDetailPage } from '../../pages/project-detail.page';
import { TasksPage } from '../../pages/tasks.page';
import { createAwsHelper } from '../../fixtures/aws-helper';
import { TEST_CONFIG, shouldSkipCleanup, shouldVerifyAws, getTimeout } from '../../config/test-config';

test.describe.serial('Full E2E Workflow', () => {
  let loginPage: LoginPage;
  let projectsPage: ProjectsPage;
  let projectDetailPage: ProjectDetailPage;
  let tasksPage: TasksPage;
  let awsHelper: ReturnType<typeof createAwsHelper>;

  let projectId: number;
  let vpcId: string | undefined;
  let clusterName: string;

  test.beforeAll(async () => {
    // Initialize AWS helper if verification enabled
    if (shouldVerifyAws()) {
      awsHelper = createAwsHelper();
    }
  });

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    projectsPage = new ProjectsPage(page);
    projectDetailPage = new ProjectDetailPage(page);
    tasksPage = new TasksPage(page);

    // Authenticate before each test — required for Tier 2 which runs serially
    const { username, password } = TEST_CONFIG.credentials.admin;
    await loginPage.loginViaApi(username, password);
  });

  test('Step 1: Create Project', async ({ page }) => {
    await projectsPage.goto();

    // Take screenshot of initial state
    await page.screenshot({ path: 'test-results/01-projects-page.png' });

    // Create project
    await projectsPage.createProject(TEST_CONFIG.testProject);

    // Verify project exists
    const projectExists = await projectsPage.verifyProjectExists(TEST_CONFIG.testProject.name);
    expect(projectExists).toBeTruthy();

    // Take screenshot after creation
    await page.screenshot({ path: 'test-results/02-project-created.png' });
  });

  test('Step 2: Add VPC Module', async ({ page }) => {
    // Navigate to project
    await projectsPage.goto();
    await projectsPage.openProject(TEST_CONFIG.testProject.name);

    // Extract project ID from URL
    const url = page.url();
    const match = url.match(/\/projects\/(\d+)/);
    expect(match).toBeTruthy();
    projectId = parseInt(match![1]);

    // Add VPC module
    await projectDetailPage.addModule(
      TEST_CONFIG.modules.vpc.path,
      TEST_CONFIG.modules.vpc.variables
    );

    // Verify module card appears
    const vpcCard = projectDetailPage.getModuleCard('vpc');
    await expect(vpcCard).toBeVisible();

    await page.screenshot({ path: 'test-results/03-vpc-module-added.png' });
  });

  test('Step 3: Add Security Module', async ({ page }) => {
    await projectDetailPage.goto(projectId);

    // Add Security module (depends on VPC)
    await projectDetailPage.addModule(
      TEST_CONFIG.modules.security.path,
      TEST_CONFIG.modules.security.variables
    );

    // Verify module card appears
    const securityCard = projectDetailPage.getModuleCard('security');
    await expect(securityCard).toBeVisible();

    await page.screenshot({ path: 'test-results/04-security-module-added.png' });
  });

  test('Step 4: Add EKS Module', async ({ page }) => {
    await projectDetailPage.goto(projectId);

    clusterName = TEST_CONFIG.modules.eks.variables.cluster_name;

    // Add EKS module (depends on VPC and Security)
    await projectDetailPage.addModule(
      TEST_CONFIG.modules.eks.path,
      TEST_CONFIG.modules.eks.variables
    );

    // Verify module card appears
    const eksCard = projectDetailPage.getModuleCard('eks');
    await expect(eksCard).toBeVisible();

    await page.screenshot({ path: 'test-results/05-eks-module-added.png' });
  });

  test('Step 5: Init VPC Module', async ({ page }) => {
    await projectDetailPage.goto(projectId);

    // Init VPC
    await projectDetailPage.initModule('vpc');

    // Wait for init to complete
    const success = await projectDetailPage.waitForModuleStatus(
      'vpc',
      'initialized',
      getTimeout('terraformInit')
    );
    expect(success).toBeTruthy();

    // Verify task in task history
    await tasksPage.goto();
    const taskResult = await tasksPage.waitForTaskCompletion(
      'vpc',
      'init',
      getTimeout('terraformInit')
    );
    expect(taskResult.success).toBeTruthy();

    await page.screenshot({ path: 'test-results/06-vpc-initialized.png' });
  });

  test('Step 6: Plan VPC Module', async ({ page }) => {
    await projectDetailPage.goto(projectId);

    // Plan VPC
    await projectDetailPage.planModule('vpc');

    // Wait for plan to complete
    await page.waitForTimeout(5000); // Give task time to queue

    await tasksPage.goto();
    const taskResult = await tasksPage.waitForTaskCompletion(
      'vpc',
      'plan',
      getTimeout('terraformPlan')
    );
    expect(taskResult.success).toBeTruthy();

    await page.screenshot({ path: 'test-results/07-vpc-planned.png' });
  });

  test('Step 7: Apply VPC Module', async ({ page }) => {
    await projectDetailPage.goto(projectId);

    // Apply VPC
    await projectDetailPage.applyModule('vpc');

    // Wait for apply to complete
    await page.waitForTimeout(5000);

    await tasksPage.goto();
    const taskResult = await tasksPage.waitForTaskCompletion(
      'vpc',
      'apply',
      getTimeout('terraformApply')
    );
    expect(taskResult.success).toBeTruthy();

    // Verify module status changed to "deployed"
    await projectDetailPage.goto(projectId);
    const status = await projectDetailPage.getModuleStatus('vpc');
    expect(status.toLowerCase()).toContain('deployed');

    await page.screenshot({ path: 'test-results/08-vpc-applied.png' });
  });

  test('Step 8: Verify VPC in AWS', async () => {
    if (!shouldVerifyAws()) {
      test.skip();
      return;
    }

    // Verify VPC exists in AWS
    const vpcResult = await awsHelper.vpcExists(TEST_CONFIG.modules.vpc.variables.vpc_cidr);
    expect(vpcResult.exists).toBeTruthy();

    vpcId = vpcResult.vpcId;
    console.log(`✅ VPC verified in AWS: ${vpcId}`);
  });

  test('Step 9: Init, Plan, Apply Security Module', async ({ page }) => {
    await projectDetailPage.goto(projectId);

    // Check dependencies are ready
    const depsReady = await projectDetailPage.areDependenciesReady('security');
    expect(depsReady).toBeTruthy();

    // Init
    await projectDetailPage.initModule('security');
    await tasksPage.goto();
    let result = await tasksPage.waitForTaskCompletion('security', 'init', getTimeout('terraformInit'));
    expect(result.success).toBeTruthy();

    // Plan
    await projectDetailPage.goto(projectId);
    await projectDetailPage.planModule('security');
    await tasksPage.goto();
    result = await tasksPage.waitForTaskCompletion('security', 'plan', getTimeout('terraformPlan'));
    expect(result.success).toBeTruthy();

    // Apply
    await projectDetailPage.goto(projectId);
    await projectDetailPage.applyModule('security');
    await tasksPage.goto();
    result = await tasksPage.waitForTaskCompletion('security', 'apply', getTimeout('terraformApply'));
    expect(result.success).toBeTruthy();

    await page.screenshot({ path: 'test-results/09-security-applied.png' });
  });

  test('Step 10: Verify Security Resources in AWS', async () => {
    if (!shouldVerifyAws() || !vpcId) {
      test.skip();
      return;
    }

    // Verify security group exists in VPC
    const sgResult = await awsHelper.securityGroupExists(vpcId);
    expect(sgResult.exists).toBeTruthy();

    console.log(`✅ Security Group verified in AWS: ${sgResult.securityGroupId}`);
  });

  test('Step 11: Init, Plan, Apply EKS Module', async ({ page }) => {
    await projectDetailPage.goto(projectId);

    // Check dependencies are ready
    const depsReady = await projectDetailPage.areDependenciesReady('eks');
    expect(depsReady).toBeTruthy();

    // Init
    await projectDetailPage.initModule('eks');
    await tasksPage.goto();
    let result = await tasksPage.waitForTaskCompletion('eks', 'init', getTimeout('terraformInit'));
    expect(result.success).toBeTruthy();

    // Plan
    await projectDetailPage.goto(projectId);
    await projectDetailPage.planModule('eks');
    await tasksPage.goto();
    result = await tasksPage.waitForTaskCompletion('eks', 'plan', getTimeout('terraformPlan'));
    expect(result.success).toBeTruthy();

    // Apply (this will take 10-15 minutes)
    await projectDetailPage.goto(projectId);
    await projectDetailPage.applyModule('eks');
    await tasksPage.goto();
    result = await tasksPage.waitForTaskCompletion('eks', 'apply', getTimeout('terraformApply'));
    expect(result.success).toBeTruthy();

    await page.screenshot({ path: 'test-results/10-eks-applied.png' });
  });

  test('Step 12: Verify EKS Cluster in AWS', async () => {
    if (!shouldVerifyAws()) {
      test.skip();
      return;
    }

    // Verify EKS cluster exists
    const eksResult = await awsHelper.eksClusterExists(clusterName);
    expect(eksResult.exists).toBeTruthy();
    expect(eksResult.status).toBe('ACTIVE');

    console.log(`✅ EKS Cluster verified in AWS: ${clusterName} (${eksResult.status})`);
  });

  test('Step 13: Verify Task History Complete', async ({ page }) => {
    await tasksPage.goto();

    // Verify all operations succeeded
    const allSucceeded = await tasksPage.verifyModuleTasksSucceeded('vpc', ['init', 'plan', 'apply']);
    expect(allSucceeded).toBeTruthy();

    const securitySucceeded = await tasksPage.verifyModuleTasksSucceeded('security', ['init', 'plan', 'apply']);
    expect(securitySucceeded).toBeTruthy();

    const eksSucceeded = await tasksPage.verifyModuleTasksSucceeded('eks', ['init', 'plan', 'apply']);
    expect(eksSucceeded).toBeTruthy();

    await page.screenshot({ path: 'test-results/11-all-tasks-complete.png' });
  });

  test('Step 14: Destroy EKS Module', async ({ page }) => {
    if (shouldSkipCleanup()) {
      console.log('⏭️  Skipping cleanup (SKIP_CLEANUP=true)');
      test.skip();
      return;
    }

    await projectDetailPage.goto(projectId);

    // Destroy EKS first (reverse dependency order)
    await projectDetailPage.destroyModule('eks');

    await tasksPage.goto();
    const result = await tasksPage.waitForTaskCompletion('eks', 'destroy', getTimeout('terraformDestroy'));
    expect(result.success).toBeTruthy();

    await page.screenshot({ path: 'test-results/12-eks-destroyed.png' });
  });

  test('Step 15: Destroy Security Module', async ({ page }) => {
    if (shouldSkipCleanup()) {
      test.skip();
      return;
    }

    await projectDetailPage.goto(projectId);

    // Destroy Security
    await projectDetailPage.destroyModule('security');

    await tasksPage.goto();
    const result = await tasksPage.waitForTaskCompletion('security', 'destroy', getTimeout('terraformDestroy'));
    expect(result.success).toBeTruthy();

    await page.screenshot({ path: 'test-results/13-security-destroyed.png' });
  });

  test('Step 16: Destroy VPC Module', async ({ page }) => {
    if (shouldSkipCleanup()) {
      test.skip();
      return;
    }

    await projectDetailPage.goto(projectId);

    // Destroy VPC last
    await projectDetailPage.destroyModule('vpc');

    await tasksPage.goto();
    const result = await tasksPage.waitForTaskCompletion('vpc', 'destroy', getTimeout('terraformDestroy'));
    expect(result.success).toBeTruthy();

    await page.screenshot({ path: 'test-results/14-vpc-destroyed.png' });
  });

  test('Step 17: Verify AWS Resources Destroyed', async () => {
    if (shouldSkipCleanup() || !shouldVerifyAws()) {
      test.skip();
      return;
    }

    // Verify VPC destroyed
    const vpcDestroyed = await awsHelper.verifyVpcDestroyed(TEST_CONFIG.modules.vpc.variables.vpc_cidr);
    expect(vpcDestroyed).toBeTruthy();

    // Verify EKS cluster destroyed
    const eksDestroyed = await awsHelper.verifyEksClusterDestroyed(clusterName);
    expect(eksDestroyed).toBeTruthy();

    console.log('✅ All AWS resources verified destroyed');
  });

  test('Step 18: Delete Test Project', async ({ page }) => {
    if (shouldSkipCleanup()) {
      console.log('⏭️  Leaving project for manual inspection');
      test.skip();
      return;
    }

    await projectsPage.goto();
    await projectsPage.deleteProject(TEST_CONFIG.testProject.name);

    // Verify project no longer exists
    const exists = await projectsPage.verifyProjectExists(TEST_CONFIG.testProject.name);
    expect(exists).toBeFalsy();

    await page.screenshot({ path: 'test-results/15-project-deleted.png' });
  });
});
