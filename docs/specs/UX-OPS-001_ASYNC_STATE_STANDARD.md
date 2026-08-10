# UX-OPS-001: Async State UX Standard

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Standardize how loading, empty, error, stale, and retry states are displayed across all operational UIs, ensuring consistent operator experience.

---

## State Definitions

| State | Condition | User Experience |
|-------|-----------|-----------------|
| **Loading** | Data is being fetched for the first time | Show skeleton/spinner |
| **Refreshing** | Data exists but is being refreshed in background | Show stale data with subtle indicator |
| **Empty** | Query succeeded but returned no items | Show empty state illustration + action |
| **Error** | Query failed | Show error message + retry button |
| **Stale** | Data exists but is older than threshold | Show data with "stale" indicator |
| **Offline** | Network connectivity lost | Show cached data with offline banner |

---

## Standard Patterns

### 1. Loading State

**First load (no cached data):**
```
┌─────────────────────────────────┐
│ ████████████  ████████          │  ← Skeleton lines
│ ████████████████  ████████      │
│ ████████  ████████████          │
└─────────────────────────────────┘
```

**Rules:**
- Use `Skeleton` component (shadcn/ui) matching the shape of the expected content
- Show skeletons for tables, cards, and detail views
- Never show a blank white page during load
- Loading should feel instant for cached data (React Query staleTime)

### 2. Refreshing State

**Background refresh (has stale data):**
```
┌─────────────────────────────────┐
│ Projects (3)          🔄 ···    │  ← Subtle spinner in header
│ ┌─────┐ ┌─────┐ ┌─────┐       │
│ │ VPC │ │ EKS │ │ WAF │       │  ← Show existing data
│ └─────┘ └─────┘ └─────┘       │
└─────────────────────────────────┘
```

**Rules:**
- Show existing data immediately
- Small spinner or "Updating..." text in the header area
- Never replace content with a full-screen loader during refresh
- Use React Query's `isFetching` (not `isLoading`) for this state

### 3. Empty State

```
┌─────────────────────────────────┐
│                                 │
│          📦                     │
│    No projects yet              │
│                                 │
│    Get started by creating      │
│    your first project.          │
│                                 │
│    [+ Create Project]           │
│                                 │
└─────────────────────────────────┘
```

**Rules:**
- Center vertically in the content area
- Include an icon/illustration relevant to the domain
- Short explanation (1-2 lines)
- Primary action button to create first item
- Never show an empty table with just headers

### 4. Error State

```
┌─────────────────────────────────┐
│                                 │
│    ⚠️ Failed to load projects   │
│                                 │
│    Could not connect to the     │
│    server. Check your network.  │
│                                 │
│    [Retry]  [Go to Dashboard]   │
│                                 │
└─────────────────────────────────┘
```

**Rules:**
- Show error message from `parseApiError()` (not raw HTTP error)
- Include a **Retry** button that calls `refetch()`
- Optionally include navigation to a safe page
- For partial failures (some data loaded), show data + error banner at top
- Never show raw stack traces or JSON error objects

### 5. Stale Data Indicator

```
┌─────────────────────────────────┐
│ Cluster Health     ⚠️ 5m stale  │
│ ┌──────┐ ┌──────┐ ┌──────┐    │
│ │ ✅   │ │ ⚠️   │ │ ✅   │    │
│ └──────┘ └──────┘ └──────┘    │
└─────────────────────────────────┘
```

**Rules:**
- Show a subtle "stale" badge when data is older than the expected refresh interval
- Use React Query's `dataUpdatedAt` to calculate staleness
- Threshold: 2x the normal poll interval (e.g., if polling every 30s, stale after 60s)

---

## React Query Configuration

### Standard Query Options

```typescript
// List queries (projects, clusters, modules)
const listQuery = useQuery({
  queryKey: ['projects'],
  queryFn: () => api.projects.getAll(),
  staleTime: 30_000,          // 30s before background refetch
  gcTime: 5 * 60_000,         // 5m cache lifetime
  refetchOnWindowFocus: true,  // Refresh when user returns to tab
  retry: 2,                    // Retry failed queries twice
});

// Detail queries (single project, single cluster)
const detailQuery = useQuery({
  queryKey: ['projects', projectId],
  queryFn: () => api.projects.get(projectId),
  staleTime: 10_000,          // 10s for detail views (fresher)
  retry: 2,
});

// Polling queries (task status, deployment progress)
const pollingQuery = useQuery({
  queryKey: ['tasks', taskId],
  queryFn: () => api.tasks.get(taskId),
  refetchInterval: 2_000,     // Poll every 2s
  refetchIntervalInBackground: false,  // Stop when tab is hidden
});
```

### State Rendering Pattern

```tsx
function ProjectList() {
  const { data, isLoading, isError, error, refetch, isFetching } = useProjects();

  if (isLoading) return <ProjectListSkeleton />;

  if (isError) return (
    <ErrorState
      message={parseApiError(error).message}
      onRetry={refetch}
    />
  );

  if (!data?.length) return (
    <EmptyState
      icon={<FolderIcon />}
      title="No projects yet"
      description="Get started by creating your first project."
      action={{ label: "Create Project", onClick: openCreateDialog }}
    />
  );

  return (
    <>
      {isFetching && <RefreshIndicator />}
      <ProjectGrid projects={data} />
    </>
  );
}
```

---

## Shared Components

| Component | Purpose | Location |
|-----------|---------|----------|
| `<Skeleton>` | Loading placeholder | `components/ui/skeleton.tsx` (shadcn) |
| `<ErrorState>` | Error display + retry | To be created in `components/shared/` |
| `<EmptyState>` | Empty data + action | To be created in `components/shared/` |
| `<RefreshIndicator>` | Background refresh spinner | To be created in `components/shared/` |

---

## Adoption Priority

1. **Dashboard** — highest visibility, most state combinations
2. **Cluster list / K8s pages** — complex async with connectivity probes
3. **Project detail** — module status polling
4. **Helm / Fleet** — fleet-wide aggregation
5. **System pages** — settings, audit log

---

## Anti-Patterns

| Don't | Do Instead |
|-------|------------|
| Show blank white page during load | Use skeleton matching content shape |
| Replace data with spinner on refresh | Show stale data + subtle indicator |
| Show empty table with only headers | Use EmptyState component |
| Show raw error JSON | Use parseApiError() for friendly message |
| Disable all UI during mutation | Disable only the trigger button |
| Auto-retry forever on error | Retry 2-3 times, then show error state |
