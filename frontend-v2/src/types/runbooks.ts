// Runbook Automation types (UX-010)

export interface RunbookStepDefinition {
  name: string;
  description: string;
}

export interface RunbookDefinition {
  id: string;
  name: string;
  description: string;
  category: 'networking' | 'platform' | 'upgrade';
  step_count: number;
  steps: RunbookStepDefinition[];
}

export interface RunbookListResponse {
  runbooks: RunbookDefinition[];
}

export interface RunbookResourceLink {
  type: string;
  name: string;
  namespace: string;
}

export interface RunbookStepResult {
  step_index: number;
  name: string;
  description: string;
  status: 'pass' | 'fail' | 'skip' | 'running';
  message: string;
  details: Record<string, unknown> | null;
  resource_link: RunbookResourceLink | null;
}

export interface RunbookExecutionResult {
  runbook_id: string;
  runbook_name: string;
  status: 'pass' | 'fail' | 'partial';
  summary: string;
  steps: RunbookStepResult[];
  cluster_id: number;
}

export interface RunbookExecuteRequest {
  cluster_id: number;
}
