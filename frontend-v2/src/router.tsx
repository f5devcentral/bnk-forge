import { createBrowserRouter, Navigate } from 'react-router-dom';
import { lazy, Suspense } from 'react';
import { AppShell } from '@/components/layout/AppShell';
import { AuthGuard } from '@/components/layout/AuthGuard';
import { ErrorBoundary, NotFound } from '@/components/ErrorBoundary';
import { Loader2 } from 'lucide-react';

// Lazy load route components for code splitting
const Login = lazy(() => import('@/pages/Login'));
const Dashboard = lazy(() => import('@/pages/Dashboard'));
const Projects = lazy(() => import('@/pages/Projects'));
const ProjectDetailV2 = lazy(() => import('@/pages/ProjectDetailV2'));
const Catalog = lazy(() => import('@/pages/Catalog'));
const TaskHistory = lazy(() => import('@/pages/TaskHistory'));
const KubernetesV2 = lazy(() => import('@/pages/KubernetesV2'));
const F5BNK = lazy(() => import('@/pages/F5BNK'));
const CNF = lazy(() => import('@/pages/CNF'));
const Stacks = lazy(() => import('@/pages/Stacks'));
const AuthTemplates = lazy(() => import('@/pages/AuthTemplates'));
const System = lazy(() => import('@/pages/System'));
const Fleet = lazy(() => import('@/pages/Fleet'));
const UserManagement = lazy(() => import('@/pages/UserManagement'));
const Benchmarks = lazy(() => import('@/pages/Benchmarks'));
const MCP = lazy(() => import('@/pages/MCP'));
const WafPolicies = lazy(() => import('@/pages/WafPolicies'));
const Infrastructure = lazy(() => import('@/pages/Infrastructure'));
const LlmDashboard = lazy(() => import('@/pages/observability/LlmDashboard'));
const LlmLogs = lazy(() => import('@/pages/observability/LlmLogs'));

export const router = createBrowserRouter([
  {
    path: '/login',
    element: (
      <Suspense fallback={
        <div className="flex items-center justify-center h-screen">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      }>
        <Login />
      </Suspense>
    ),
    errorElement: <ErrorBoundary />,
  },
  {
    path: '/',
    element: (
      <AuthGuard>
        <AppShell />
      </AuthGuard>
    ),
    errorElement: <ErrorBoundary />,
    children: [
      {
        index: true,
        element: <Dashboard />,
      },
      {
        path: 'projects',
        element: <Projects />,
      },
      {
        path: 'projects/:id',
        element: <ProjectDetailV2 />,
      },
      {
        path: 'projects/:id/:section',
        element: <ProjectDetailV2 />,
      },
      {
        // Old /modules URL now lives under the tabbed Catalog page.
        path: 'modules',
        element: <Navigate to="/catalog" replace />,
      },
      {
        path: 'catalog',
        element: <Catalog />,
      },
      {
        path: 'catalog/modules',
        element: <Navigate to="/catalog" replace />,
      },
      {
        path: 'catalog/module-library',
        element: <Navigate to="/catalog" replace />,
      },
      {
        path: 'catalog/blueprint-catalog',
        element: <Navigate to="/catalog?tab=blueprints" replace />,
      },
      {
        path: 'catalog/blueprints',
        element: <Navigate to="/catalog?tab=blueprints" replace />,
      },
      {
        path: 'catalog/bfb-images',
        element: <Navigate to="/catalog?tab=bfb-images" replace />,
      },
      {
        path: 'catalog/bf-conf-templates',
        element: <Navigate to="/catalog?tab=bf-conf-templates" replace />,
      },
      {
        path: 'tasks',
        element: <TaskHistory />,
      },
      {
        path: 'kubernetes',
        element: <KubernetesV2 />,
      },
      {
        // K8S-UX-004: /helm redirects to /kubernetes — Helm is now integrated into K8s page
        path: 'helm',
        element: <Navigate to="/kubernetes" replace />,
      },
      {
        path: 'bnk',
        element: <F5BNK />,
      },
      {
        path: 'cnf',
        element: <CNF />,
      },
      {
        path: 'stacks',
        element: <Stacks />,
      },
      {
        path: 'auth-templates',
        element: <AuthTemplates />,
      },
      {
        path: 'system',
        element: <System />,
      },
      {
        // K8S-UX-005: Operators merged into Fleet page
        path: 'operators',
        element: <Navigate to="/fleet?tab=operators" replace />,
      },
      {
        path: 'fleet',
        element: <Fleet />,
      },
      {
        // D-022 P6 IA: new top-level Infrastructure section (DPU/BlueField + bare-metal hosts).
        path: 'infrastructure',
        element: <Infrastructure />,
      },
      {
        path: 'benchmarks',
        element: <Benchmarks />,
      },
      {
        path: 'waf-policies',
        element: <WafPolicies />,
      },
      {
        path: 'observability/ai-gateway',
        element: <LlmDashboard />,
      },
      {
        path: 'observability/ai-gateway/logs',
        element: <LlmLogs />,
      },
      {
        path: 'mcp-server',
        element: <MCP />,
      },
      {
        path: 'users',
        element: <UserManagement />,
      },
      {
        path: '*',
        element: <NotFound />,
      },
    ],
  },
], {
  future: {
    v7_relativeSplatPath: true,
  },
});
